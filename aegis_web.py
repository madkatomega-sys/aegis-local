"""An origin-checking proxy in front of OpenCode's browser server, for `aegis.py opencode --web`.

OpenCode 1.19.5's server checks its password but not where a request comes from. A web page open
in another tab of a signed-in browser could therefore send it a "simple" cross-site POST, and the
browser would attach the cached password (the 0.3.0 validation saw one create a session). This
proxy sits on the port the user opens and refuses, before anything reaches OpenCode:

- a `Host` other than this port on `127.0.0.1`, `localhost` or `[::1]` (DNS rebinding);
- an `Origin` that isn't this port on those hosts, including `null`;
- `Sec-Fetch-Site` other than `same-origin` or `none`, when there is no `Origin`;
- a cross-origin `Referer`, when there is neither;
- a request that changes state (POST, PUT, PATCH, DELETE) without `Content-Type: application/json`
  and without a same-origin `Origin`, the shape of a request a page can send without a preflight.

Everything else passes through unchanged, the password included, so OpenCode still checks it:
plain requests, the `/event` stream (streamed as it arrives), and WebSocket upgrades (the
terminal), tunnelled both ways. OpenCode itself listens on another loopback port. The browser
caches the password for the proxy's origin only, so a page reaching OpenCode's port directly
carries no password and gets a 401.

Standard library only.
"""

from __future__ import annotations

import contextlib
import http.client
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
# Hop-by-hop headers (RFC 9110 7.6.1) are about one connection, so they aren't passed on.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade"}
CHUNK = 64 * 1024


def allowed_hosts(port: int) -> set[str]:
    return {f"{name}:{port}" for name in LOOPBACK_NAMES}


def allowed_origins(port: int) -> set[str]:
    return {f"http://{host}" for host in allowed_hosts(port)}


def origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def refusal(method: str, headers: dict[str, str], port: int) -> str | None:
    """Why this request must not reach OpenCode, or None if it may. `headers` keys are lowercase."""
    host = headers.get("host", "").strip().lower()
    if host not in allowed_hosts(port):
        return f"Host {host or '(none)'!r} isn't this server; open http://127.0.0.1:{port}/"
    origin = headers.get("origin", "").strip().lower()
    if origin:
        if origin not in allowed_origins(port):
            return f"requests from {origin} aren't allowed"
    else:
        site = headers.get("sec-fetch-site", "").strip().lower()
        if site and site not in ("same-origin", "none"):
            return f"a {site} request isn't allowed"
        referer = headers.get("referer", "").strip()
        if referer and origin_of(referer) not in allowed_origins(port):
            return "a request from another site isn't allowed"
    if method.upper() in STATE_CHANGING and not origin:
        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        length = headers.get("content-length", "0").strip() or "0"
        if content_type != "application/json" and length != "0":
            return f"{method.upper()} needs Content-Type: application/json"
    return None


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.0: each response ends when its connection closes, so streamed bodies (the event
    # stream, chunked replies) need no re-framing.
    protocol_version = "HTTP/1.0"
    server: _Server

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - the stdlib's own signature
        pass  # OpenCode logs requests itself

    def _headers(self) -> dict[str, str]:
        return {k.lower(): v for k, v in self.headers.items()}

    def _refuse(self, reason: str) -> None:
        body = f"Aegis refused this request: {reason}\n".encode()
        self.send_response(403)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        headers = self._headers()
        reason = refusal(self.command, headers, self.server.public_port)
        if reason:
            self._refuse(reason)
            return
        if "upgrade" in headers.get("connection", "").lower() and "upgrade" in headers:
            self._tunnel()
            return
        self._forward()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _handle  # noqa: N815

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if self.headers.get("Transfer-Encoding"):
            self.send_error(411, "send a Content-Length")
            return
        body = self.rfile.read(length) if length else None
        upstream = http.client.HTTPConnection("127.0.0.1", self.server.upstream_port, timeout=None)
        out = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP | {"host"}}
        out["Host"] = f"127.0.0.1:{self.server.upstream_port}"
        try:
            upstream.request(self.command, self.path, body=body, headers=out)
            reply = upstream.getresponse()
        except OSError as exc:
            self.send_error(502, f"OpenCode isn't answering: {exc}")
            return
        try:
            self.send_response_only(reply.status, reply.reason)
            for key, value in reply.getheaders():
                if key.lower() in HOP_BY_HOP:
                    continue
                if key.lower() == "location":
                    value = self._public_location(value)
                self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while chunk := reply.read1(CHUNK):
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError:
            pass  # the browser went away (a closed tab ends the event stream this way)
        finally:
            upstream.close()

    def _public_location(self, value: str) -> str:
        for name in LOOPBACK_NAMES:
            value = value.replace(f"//{name}:{self.server.upstream_port}", f"//{name}:{self.server.public_port}")
        return value

    def _tunnel(self) -> None:
        try:
            upstream = socket.create_connection(("127.0.0.1", self.server.upstream_port))
        except OSError as exc:
            self.send_error(502, f"OpenCode isn't answering: {exc}")
            return
        lines = [f"{self.command} {self.path} HTTP/1.1"]
        for key, value in self.headers.items():
            if key.lower() == "host":
                value = f"127.0.0.1:{self.server.upstream_port}"
            lines.append(f"{key}: {value}")
        upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        client = self.connection

        def pump(read, sink: socket.socket) -> None:
            try:
                while data := read(CHUNK):
                    sink.sendall(data)
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    sink.shutdown(socket.SHUT_WR)

        to_upstream = threading.Thread(target=pump, args=(self.rfile.read1, upstream), daemon=True)
        to_upstream.start()
        pump(upstream.recv, client)
        to_upstream.join(timeout=5)
        upstream.close()
        self.close_connection = True


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, public_port: int, upstream_port: int) -> None:
        self.upstream_port = upstream_port
        super().__init__(("127.0.0.1", public_port), _Handler)
        self.public_port = self.server_address[1]  # the real port, when 0 asked for any free one


class Proxy:
    """`with Proxy(public, upstream):` serves the origin check on 127.0.0.1:public until the block ends."""

    def __init__(self, public_port: int, upstream_port: int) -> None:
        self.server = _Server(public_port, upstream_port)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Proxy:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
