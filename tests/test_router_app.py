"""HTTP-level tests for the model router with a fake llama-server behind it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from aegis_router import app as router_module

SSE_EVENTS = [
    b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
    b"data: [DONE]\n\n",
]
SSE = b"".join(SSE_EVENTS)


async def sse_stream():
    for event in SSE_EVENTS:
        yield event


class FakeTiers:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.mode = "ok"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "ok"})
        body = json.loads(request.content)
        self.requests.append((request.url.host, body))
        if self.mode == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if self.mode == "html":
            return httpx.Response(503, text="<html>Loading model</html>", headers={"content-type": "text/html"})
        if body.get("stream"):
            # A generator body, so httpx streams it the way a real llama-server response arrives.
            return httpx.Response(200, content=sse_stream(), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]})


@pytest.fixture
def router(repo_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(router_module, "CONFIG_PATH", repo_root / "config" / "router.yaml")
    monkeypatch.setenv("AEGIS_TIER0_URL", "http://tier0.test/v1")
    monkeypatch.setenv("AEGIS_TIER1_URL", "http://tier1.test/v1")
    fakes = FakeTiers()
    with TestClient(router_module.app) as client:
        router_module.app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(fakes))
        yield client, fakes


def chat(client: TestClient, model: str, **extra):
    return client.post(
        "/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": "hi"}]} | extra
    )


def test_models_lists_aliases(router):
    client, _ = router
    ids = {m["id"] for m in client.get("/v1/models").json()["data"]}
    assert ids == {"aegis-reasoner", "aegis-fast", "aegis-extractor"}


def test_alias_routes_and_caps_tokens(router):
    client, fakes = router
    resp = chat(client, "aegis-fast", max_tokens=999_999)
    assert resp.status_code == 200
    assert resp.headers["x-aegis-tier"] == "tier0"
    host, body = fakes.requests[-1]
    assert host == "tier0.test"
    assert body["model"] == "tier0" and body["max_tokens"] == 2048


def test_extractor_gets_the_schema_and_rejects_tools(router):
    client, fakes = router
    chat(client, "aegis-extractor", response_format={"type": "text"})
    _, body = fakes.requests[-1]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "extraction"
    resp = chat(client, "aegis-extractor", tools=[{"type": "function", "function": {"name": "x"}}])
    assert resp.status_code == 400


def test_streaming_passes_through_unchanged(router):
    client, _ = router
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "aegis-reasoner", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["x-aegis-tier"] == "tier1"
        assert b"".join(resp.iter_raw()) == SSE


def test_unreachable_tier_is_a_502(router):
    client, fakes = router
    fakes.mode = "down"
    resp = chat(client, "aegis-reasoner")
    assert resp.status_code == 502
    assert "tier1 unavailable" in resp.json()["error"]["message"]


def test_non_json_tier_response_is_a_502_not_a_crash(router):
    client, fakes = router
    fakes.mode = "html"
    resp = chat(client, "aegis-reasoner")
    assert resp.status_code == 502
    assert "non-JSON" in resp.json()["error"]["message"]


def test_bad_request_bodies(router):
    client, _ = router
    assert client.post("/v1/chat/completions", content=b"not json").status_code == 400
    assert client.post("/v1/chat/completions", json=[1, 2]).status_code == 400


def test_status_reports_tier_health(router):
    client, _ = router
    status = client.get("/status").json()
    assert status["tiers"]["tier0"]["healthy"] and status["tiers"]["tier1"]["healthy"]
