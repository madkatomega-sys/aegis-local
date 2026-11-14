"""Minimal Streamable HTTP MCP client for talking to upstream tool servers.

Only what the guard needs: initialize, tools/list, tools/call. Responses may arrive as plain
JSON or as a server-sent event stream; both are handled.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from aegis_guard import __version__
from aegis_guard.policy import Trust

PROTOCOL_VERSION = "2025-06-18"
_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")


class UpstreamError(RuntimeError):
    pass


def expand_env(value: str) -> str:
    return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)


@dataclass
class ServerSpec:
    name: str
    url: str
    trust: Trust
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load_all(cls, raw: dict[str, Any]) -> list[ServerSpec]:
        specs = []
        for name, entry in raw.get("servers", {}).items():
            if "__" in name:
                raise ValueError(f"server name {name!r} may not contain '__'")
            enabled = bool(entry.get("enabled", False))
            env_flag = entry.get("enabled_env")
            if env_flag and os.environ.get(env_flag, "").lower() in {"1", "true", "yes"}:
                enabled = True
            if not enabled:
                continue
            specs.append(
                cls(
                    name=name,
                    url=str(entry["url"]),
                    trust=Trust(entry.get("trust", "untrusted")),
                    headers={k: expand_env(str(v)) for k, v in entry.get("headers", {}).items()},
                )
            )
        return specs


def _parse_response(resp: httpx.Response, request_id: int) -> dict[str, Any]:
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                message = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        raise UpstreamError("no matching response in event stream")
    return resp.json()


class UpstreamClient:
    def __init__(self, spec: ServerSpec, client: httpx.AsyncClient) -> None:
        self.spec = spec
        self._client = client
        self._session_id: str | None = None
        self._initialized = False
        self._ids = itertools.count(1)

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = next(self._ids)
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self.spec.headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        try:
            resp = await self._client.post(self.spec.url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{self.spec.name}: {exc}") from exc
        if resp.status_code == 404 and self._session_id:
            self._session_id = None  # session expired upstream; caller may re-initialize
            self._initialized = False
            raise UpstreamError(f"{self.spec.name}: session expired")
        if resp.status_code >= 400:
            raise UpstreamError(f"{self.spec.name}: HTTP {resp.status_code}")
        if sid := resp.headers.get("mcp-session-id"):
            self._session_id = sid
        message = _parse_response(resp, request_id)
        if "error" in message:
            raise UpstreamError(f"{self.spec.name}: {message['error'].get('message', 'error')}")
        return message.get("result", {})

    async def _notify(self, method: str) -> None:
        headers = {"Content-Type": "application/json", "MCP-Protocol-Version": PROTOCOL_VERSION, **self.spec.headers}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        with contextlib.suppress(httpx.HTTPError):
            await self._client.post(self.spec.url, json={"jsonrpc": "2.0", "method": method}, headers=headers)

    async def initialize(self) -> None:
        await self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "aegis-guard", "version": __version__},
            },
        )
        await self._notify("notifications/initialized")
        self._initialized = True

    async def ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.ensure_initialized()
        tools: list[dict[str, Any]] = []
        cursor = None
        while True:
            result = await self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self.ensure_initialized()
        try:
            return await self._rpc("tools/call", {"name": name, "arguments": arguments})
        except UpstreamError:
            if not self._initialized:  # expired; retry once with a fresh session
                await self.initialize()
                return await self._rpc("tools/call", {"name": name, "arguments": arguments})
            raise
