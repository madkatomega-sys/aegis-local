"""OpenAI-compatible router in front of the llama-server tiers."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from aegis_router import __version__
from aegis_router.routing import RouterConfig, RoutingError, prepare_upstream_body, select_route

CONFIG_PATH = Path(os.environ.get("AEGIS_ROUTER_CONFIG", "/config/router.yaml"))
TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=60.0, pool=5.0)


def load_config() -> RouterConfig:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return RouterConfig.from_dict(yaml.safe_load(fh))


def upstream_url(config: RouterConfig, tier_name: str) -> str:
    env = config.tiers[tier_name].upstream_env
    url = os.environ.get(env, "").rstrip("/")
    if not url:
        raise RoutingError(f"{tier_name} is not configured ({env} is empty)", status=503)
    return url


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.config = load_config()
    app.state.client = httpx.AsyncClient(timeout=TIMEOUT)
    yield
    await app.state.client.aclose()


app = FastAPI(title="aegis-model-router", version=__version__, lifespan=lifespan)


def error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": "aegis_router_error"}}, status_code=status)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/status")
async def status(request: Request) -> dict:
    config: RouterConfig = request.app.state.config
    client: httpx.AsyncClient = request.app.state.client
    tiers = {}
    for name in config.tiers:
        try:
            base = upstream_url(config, name)
            root = base[: -len("/v1")] if base.endswith("/v1") else base
            resp = await client.get(f"{root}/health", timeout=3.0)
            tiers[name] = {"url": base, "healthy": resp.status_code == 200}
        except (RoutingError, httpx.HTTPError) as exc:
            tiers[name] = {"healthy": False, "error": str(exc)}
    return {"version": __version__, "tiers": tiers, "aliases": sorted(config.aliases)}


@app.get("/v1/models")
async def models(request: Request) -> dict:
    config: RouterConfig = request.app.state.config
    return {
        "object": "list",
        "data": [{"id": name, "object": "model", "owned_by": "aegis-local"} for name in sorted(config.aliases)],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    config: RouterConfig = request.app.state.config
    client: httpx.AsyncClient = request.app.state.client
    try:
        body = await request.json()
    except ValueError:
        return error("request body must be JSON", 400)
    if not isinstance(body, dict):
        return error("request body must be a JSON object", 400)

    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        route = select_route(config, body, headers)
        url = f"{upstream_url(config, route.tier.name)}/chat/completions"
    except RoutingError as exc:
        return error(str(exc), exc.status)

    payload = prepare_upstream_body(body, route)
    trace = {"x-aegis-tier": route.tier.name, "x-aegis-route": route.reason}

    if payload.get("stream"):
        upstream_req = client.build_request("POST", url, json=payload)
        try:
            upstream = await client.send(upstream_req, stream=True)
        except httpx.HTTPError as exc:
            return error(f"{route.tier.name} unavailable: {exc}", 502)

        async def relay():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            relay(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers=trace,
        )

    try:
        upstream = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        return error(f"{route.tier.name} unavailable: {exc}", 502)
    try:
        data = upstream.json()
    except ValueError:
        snippet = upstream.text[:200]
        return error(f"{route.tier.name} returned HTTP {upstream.status_code} with a non-JSON body: {snippet}", 502)
    return JSONResponse(data, status_code=upstream.status_code, headers=trace)
