"""MCP guard proxy: the only MCP server the IDE talks to.

Speaks Streamable HTTP MCP to the client (JSON responses, no server-initiated streams) and fans
out to upstream servers listed in mcp-servers.yaml. Every tools/call passes through, in order:

    policy (deny / hold / allow)  ->  circuit breaker  ->  upstream  ->  taint + quarantine  ->  ledger

Two bearer tokens protect it. The client token (given to OpenCode) is required on /mcp and
/status; the admin token (held only by aegis.py) is required to approve calls or reset sessions.
Sandbox containers can reach this service over their networks but hold neither token.
"""

from __future__ import annotations

import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from aegis_guard import __version__
from aegis_guard.breaker import BreakerConfig, CycleBreaker
from aegis_guard.canonical import call_digest, content_digest
from aegis_guard.ledger import Ledger
from aegis_guard.policy import Action, Policy, SessionTaint, Trust
from aegis_guard.quarantine import build_extraction_request, content_to_text, parse_extraction, render_quarantined
from aegis_guard.upstream import ServerSpec, UpstreamClient, UpstreamError

log = logging.getLogger("aegis.guard")

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26")


@dataclass
class SessionState:
    breaker: CycleBreaker
    taint: SessionTaint = field(default_factory=SessionTaint)


def _read_yaml(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _require_token(name: str) -> str:
    token = os.environ.get(name, "")
    if len(token) < 24:
        raise RuntimeError(f"{name} is missing or too short; start the stack with `python aegis.py up`")
    return token


@asynccontextmanager
async def lifespan(app: FastAPI):
    raw_policy = _read_yaml(os.environ.get("AEGIS_POLICY", "/config/policy.yaml"))
    raw_servers = _read_yaml(os.environ.get("AEGIS_SERVERS", "/config/mcp-servers.yaml"))
    app.state.client_token = _require_token("AEGIS_CLIENT_TOKEN")
    app.state.admin_token = _require_token("AEGIS_ADMIN_TOKEN")
    app.state.policy = Policy.from_dict(raw_policy)
    app.state.breaker_config = BreakerConfig.from_policy(raw_policy)
    app.state.router_url = os.environ.get("AEGIS_ROUTER_URL", "http://model-router:8000/v1").rstrip("/")
    app.state.ledger = Ledger(os.environ.get("AEGIS_LEDGER", "/data/ledger.sqlite"))
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0))
    app.state.upstreams = {spec.name: UpstreamClient(spec, app.state.http) for spec in ServerSpec.load_all(raw_servers)}
    app.state.sessions: dict[str, SessionState] = {}
    app.state.tool_cache: list[dict[str, Any]] | None = None
    yield
    await app.state.http.aclose()
    app.state.ledger.close()


app = FastAPI(title="aegis-mcp-guard-proxy", version=__version__, lifespan=lifespan)


# -- helpers ---------------------------------------------------------------------------------


def _authorized(request: Request, expected: str) -> bool:
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def _rpc_result(request_id: Any, result: dict[str, Any], headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result}, headers=headers)


def _rpc_error(request_id: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}, status_code=status
    )


def _tool_error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


async def _all_tools(app: FastAPI) -> list[dict[str, Any]]:
    if app.state.tool_cache is not None:
        return app.state.tool_cache
    policy: Policy = app.state.policy
    tools: list[dict[str, Any]] = []
    complete = True
    for name, upstream in app.state.upstreams.items():
        try:
            for tool in await upstream.list_tools():
                exposed = f"{name}__{tool['name']}"
                if policy.is_visible(exposed):
                    tools.append({**tool, "name": exposed})
        except UpstreamError as exc:
            complete = False
            log.warning("could not list tools from %s: %s", name, exc)
    if complete:
        app.state.tool_cache = tools
    return tools


async def _quarantine(app: FastAPI, tool: str, source: str, result: dict[str, Any]) -> dict[str, Any]:
    policy: Policy = app.state.policy
    raw_text = content_to_text(result.get("content", []))
    body = build_extraction_request(tool, raw_text, policy.taint)
    try:
        resp = await app.state.http.post(
            f"{app.state.router_url}/chat/completions", json=body, headers={"X-Aegis-Task": "extract"}
        )
        resp.raise_for_status()
        extraction = parse_extraction(resp.json(), policy.taint)
    except (httpx.HTTPError, ValueError) as exc:
        if policy.taint.on_extractor_failure == "raw_with_warning":
            warning = {"type": "text", "text": f"[AEGIS] Quarantine failed ({exc}). Raw untrusted content follows."}
            return {**result, "content": [warning, *result.get("content", [])]}
        return _tool_error(
            f"[AEGIS] Output of {tool} came from an untrusted source and could not be quarantined: {exc}"
        )
    return {"content": render_quarantined(tool, source, extraction), "isError": bool(result.get("isError"))}


async def _call_tool(app: FastAPI, session_id: str, state: SessionState, params: dict[str, Any]) -> dict[str, Any]:
    policy: Policy = app.state.policy
    ledger: Ledger = app.state.ledger
    name = str(params.get("name", ""))
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _tool_error("[AEGIS] Tool arguments must be an object.")
    digest = call_digest(name, arguments)
    server, _, tool = name.partition("__")
    upstream: UpstreamClient | None = app.state.upstreams.get(server)

    def record(decision: str, **kw: Any) -> None:
        ledger.log(session=session_id, tool=name, digest=digest, decision=decision, tainted=state.taint.tainted, **kw)

    if upstream is None or not tool:
        state.breaker.note_attempt()
        record("deny", reason="unknown tool")
        return _tool_error(f"[AEGIS] Unknown tool: {name}")

    verdict = policy.decide(name, state.taint)
    if verdict.action is Action.DENY:
        state.breaker.note_attempt()
        record("deny", rule=verdict.rule, reason=verdict.reason)
        return _tool_error(f"[AEGIS] Denied: {verdict.reason}.")
    if verdict.action is Action.HOLD and not ledger.consume_approval(session_id, digest):
        state.breaker.note_attempt()
        approval_id = ledger.request_approval(
            session=session_id,
            tool=name,
            digest=digest,
            arguments=arguments,
            reason=verdict.reason,
            taint_sources=sorted(state.taint.sources),
        )
        record("hold", rule=verdict.rule, reason=verdict.reason)
        return _tool_error(
            f"[AEGIS] Held for approval (id {approval_id}): {verdict.reason}. "
            f"Tell the user what you want to do and why. They can run `python aegis.py approve {approval_id}`; "
            "after that, repeat this exact call once."
        )

    gate = state.breaker.check(name, arguments)
    if not gate.allowed:
        record("trip", rule=gate.rule, reason=gate.reason)
        return _tool_error(
            f"[AEGIS] Circuit breaker tripped ({gate.rule}): {gate.reason}. This session's tool access is stopped. "
            "Summarize what you tried and what failed, and ask the user how to proceed."
        )

    try:
        result = await upstream.call_tool(tool, arguments)
    except UpstreamError as exc:
        state.breaker.record_result(digest, ok=False)
        record("error", reason=str(exc), ok=False)
        return _tool_error(f"[AEGIS] {exc}")

    ok = not result.get("isError", False)
    state.breaker.record_result(digest, ok=ok)
    if upstream.spec.trust is Trust.UNTRUSTED:
        state.taint.mark(server)
        if policy.taint.quarantine:
            result = await _quarantine(app, name, server, result)
    record("allow", rule=verdict.rule, ok=ok, result_digest=content_digest(result.get("content", [])))
    return result


# -- MCP endpoint ----------------------------------------------------------------------------


@app.post("/mcp")
async def mcp_post(request: Request) -> Response:
    if not _authorized(request, request.app.state.client_token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        message = await request.json()
    except ValueError:
        return _rpc_error(None, -32700, "parse error", status=400)
    if isinstance(message, list):
        return _rpc_error(None, -32600, "batch requests are not supported", status=400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "invalid request", status=400)

    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    sessions: dict[str, SessionState] = request.app.state.sessions

    if request_id is None:  # notification or response from client
        return Response(status_code=202)

    if method == "initialize":
        session_id = uuid.uuid4().hex
        sessions[session_id] = SessionState(breaker=CycleBreaker(config=request.app.state.breaker_config))
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return _rpc_result(
            request_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "aegis-guard", "version": __version__},
                "instructions": (
                    "Tools are prefixed by server (math__, code__, writing__, github__). Calls are recorded; "
                    "repeated failing calls stop the session; some calls are held for the user's approval."
                ),
            },
            headers={"Mcp-Session-Id": session_id},
        )

    session_id = request.headers.get("mcp-session-id", "")
    state = sessions.get(session_id)
    if state is None:
        return _rpc_error(request_id, -32001, "unknown or expired session", status=404)

    if method == "ping":
        return _rpc_result(request_id, {})
    if method == "tools/list":
        return _rpc_result(request_id, {"tools": await _all_tools(request.app)})
    if method == "tools/call":
        return _rpc_result(request_id, await _call_tool(request.app, session_id, state, params))
    return _rpc_error(request_id, -32601, f"method not found: {method}")


@app.get("/mcp")
async def mcp_get() -> Response:
    return Response(status_code=405)  # no server-initiated streams


@app.delete("/mcp")
async def mcp_delete(request: Request) -> Response:
    if not _authorized(request, request.app.state.client_token):
        return Response(status_code=401)
    request.app.state.sessions.pop(request.headers.get("mcp-session-id", ""), None)
    return Response(status_code=204)


# -- status and admin ------------------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/status")
async def status(request: Request) -> Response:
    if not (
        _authorized(request, request.app.state.client_token) or _authorized(request, request.app.state.admin_token)
    ):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    sessions = {
        sid: {
            "attempts": s.breaker.attempts,
            "remaining_budget": s.breaker.remaining_budget,
            "tainted": s.taint.tainted,
            "taint_sources": sorted(s.taint.sources),
            "tripped": None
            if s.breaker.tripped is None
            else {"rule": s.breaker.tripped.rule, "reason": s.breaker.tripped.reason},
        }
        for sid, s in request.app.state.sessions.items()
    }
    return JSONResponse(
        {
            "version": __version__,
            "upstreams": sorted(request.app.state.upstreams),
            "sessions": sessions,
            "pending_approvals": len(request.app.state.ledger.pending()),
        }
    )


@app.get("/admin/approvals")
async def list_approvals(request: Request) -> Response:
    if not _authorized(request, request.app.state.admin_token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({"pending": [p.__dict__ for p in request.app.state.ledger.pending()]})


@app.post("/admin/approvals/{approval_id}/{verdict}")
async def decide_approval(request: Request, approval_id: str, verdict: str) -> Response:
    if not _authorized(request, request.app.state.admin_token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if verdict not in {"approve", "deny"}:
        return JSONResponse({"error": "verdict must be approve or deny"}, status_code=400)
    changed = request.app.state.ledger.decide(approval_id, approve=verdict == "approve")
    return JSONResponse({"id": approval_id, "updated": changed}, status_code=200 if changed else 404)


@app.post("/admin/sessions/{session_id}/reset")
async def reset_session(request: Request, session_id: str) -> Response:
    if not _authorized(request, request.app.state.admin_token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    state = request.app.state.sessions.get(session_id)
    if state is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    state.breaker.reset()  # taint is deliberately kept: it never clears within a session
    return JSONResponse({"id": session_id, "reset": True})
