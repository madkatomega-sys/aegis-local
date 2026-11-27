"""aegis_web.py: the origin check in front of OpenCode's browser server."""

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import aegis_web

PORT = 4096
SAME = "http://127.0.0.1:4096"


def headers(**extra: str) -> dict[str, str]:
    base = {"host": "127.0.0.1:4096"}
    base.update({k.replace("_", "-"): v for k, v in extra.items()})
    return base


# -- the decision ------------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1:4096", "localhost:4096", "[::1]:4096", "LOCALHOST:4096"])
def test_loopback_hosts_on_this_port_pass(host):
    assert aegis_web.refusal("GET", {"host": host}, PORT) is None


@pytest.mark.parametrize(
    "host", ["", "evil.example", "evil.example:4096", "127.0.0.1:4097", "127.0.0.1", "0.0.0.0:4096"]
)
def test_other_hosts_are_refused(host):
    """A DNS-rebinding page reaches 127.0.0.1 under its own name, which shows up in Host."""
    assert "Host" in aegis_web.refusal("GET", {"host": host} if host else {}, PORT)


@pytest.mark.parametrize("origin", [SAME, "http://localhost:4096", "http://[::1]:4096"])
def test_same_origin_requests_pass(origin):
    assert aegis_web.refusal("POST", headers(origin=origin, content_type="application/json"), PORT) is None
    assert (
        aegis_web.refusal("POST", headers(origin=origin, content_type="text/plain", content_length="2"), PORT) is None
    )


@pytest.mark.parametrize(
    "origin", ["http://attacker.example", "null", "http://127.0.0.1:4097", "https://127.0.0.1:4096", "http://127.0.0.1"]
)
def test_other_origins_are_refused(origin):
    assert aegis_web.refusal("GET", headers(origin=origin), PORT)
    assert aegis_web.refusal("POST", headers(origin=origin, content_type="application/json"), PORT)


def test_the_0_3_cross_site_post_is_refused():
    """What 0.3's validation sent: a simple text/plain POST from another origin, with the password."""
    req = headers(
        origin="http://attacker.example", content_type="text/plain", content_length="2", authorization="Basic x"
    )
    assert "attacker.example" in aegis_web.refusal("POST", req, PORT)


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_cross_site_fetch_metadata_is_refused_without_an_origin(site):
    assert site in aegis_web.refusal("GET", headers(sec_fetch_site=site), PORT)


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_same_origin_fetch_metadata_passes(site):
    assert aegis_web.refusal("GET", headers(sec_fetch_site=site), PORT) is None


def test_a_cross_site_referer_is_refused_without_an_origin():
    assert aegis_web.refusal("GET", headers(referer="http://attacker.example/page"), PORT)
    assert aegis_web.refusal("GET", headers(referer=SAME + "/session/abc"), PORT) is None


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_a_state_change_without_an_origin_must_be_json(method):
    """A page can send text/plain, form or multipart bodies without a preflight; JSON needs one."""
    for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", ""):
        assert "application/json" in aegis_web.refusal(
            method, headers(content_type=content_type, content_length="2"), PORT
        )
    assert (
        aegis_web.refusal(method, headers(content_type="application/json; charset=utf-8", content_length="2"), PORT)
        is None
    )


def test_an_empty_post_without_an_origin_passes():
    """Tools such as the release check abort a chat with an empty POST; a page could do no more with it."""
    assert aegis_web.refusal("POST", headers(content_length="0"), PORT) is None


def test_reads_without_an_origin_pass():
    """curl, scripts, and the browser's own top-level navigation send no Origin on GET."""
    assert aegis_web.refusal("GET", headers(), PORT) is None


# -- the proxy, against a stand-in for OpenCode -------------------------------------------------


class FakeOpenCode(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list[dict] = []

    def log_message(self, *args):
        pass

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        FakeOpenCode.seen.append(
            {"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body}
        )
        return body

    def do_GET(self):  # noqa: N802
        self._record()
        if self.path == "/event":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for n in range(3):
                data = f"data: {n}\n\n".encode()
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
                time.sleep(0.3)
            self.wfile.write(b"0\r\n\r\n")
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.flush()
            while data := self.connection.recv(1024):  # echo, like a terminal would
                self.connection.sendall(data.upper())
            self.close_connection = True
            return
        body = json.dumps({"path": self.path, "auth": self.headers.get("Authorization")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        body = self._record()
        reply = json.dumps({"echo": body.decode()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


@pytest.fixture
def proxied():
    FakeOpenCode.seen = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenCode)
    upstream.daemon_threads = True
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    with aegis_web.Proxy(0, upstream.server_address[1]) as proxy:
        yield proxy.port, upstream.server_address[1]
    upstream.shutdown()


def call(port, method="GET", path="/", body=None, **extra):
    hdrs = {k.replace("_", "-").title(): v for k, v in extra.items()}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def test_a_plain_request_passes_through_with_the_password(proxied):
    port, upstream = proxied
    status, _, body = call(port, path="/session?x=1", authorization="Basic c2VjcmV0")
    assert status == 200
    assert json.loads(body) == {"path": "/session?x=1", "auth": "Basic c2VjcmV0"}  # OpenCode still checks it
    assert FakeOpenCode.seen[-1]["headers"]["Host"] == f"127.0.0.1:{upstream}"


def test_a_json_post_passes_through(proxied):
    port, _ = proxied
    status, _, body = call(
        port, "POST", "/session", b'{"a": 1}', content_type="application/json", origin=f"http://127.0.0.1:{port}"
    )
    assert status == 200 and json.loads(body) == {"echo": '{"a": 1}'}


def test_the_cross_site_post_never_reaches_opencode(proxied):
    port, _ = proxied
    status, _, body = call(port, "POST", "/session", b"{}", content_type="text/plain", origin="http://attacker.example",
                           authorization="Basic c2VjcmV0")  # fmt: skip
    assert status == 403 and b"attacker.example" in body
    assert FakeOpenCode.seen == []


def test_a_rebound_host_never_reaches_opencode(proxied):
    port, _ = proxied
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.sendall(b"GET / HTTP/1.1\r\nHost: evil.example:4096\r\n\r\n")
        reply = s.recv(4096)
    assert reply.startswith(b"HTTP/1.0 403")
    assert FakeOpenCode.seen == []


def test_the_event_stream_arrives_as_it_is_sent(proxied):
    port, _ = proxied
    started = time.monotonic()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/event")
    with urllib.request.urlopen(req, timeout=10) as resp:
        first = resp.readline()
        first_at = time.monotonic() - started
        rest = resp.read()
    assert first == b"data: 0\n"
    assert first_at < 0.5  # not held back until the stream ends (about 0.9 s)
    assert b"data: 2" in rest


def test_redirects_point_at_the_proxy(proxied):
    port, upstream = proxied
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.sendall(f"GET /redirect HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
        reply = s.recv(4096).decode()
    assert f"Location: http://127.0.0.1:{port}/elsewhere" in reply
    assert str(upstream) not in reply


def test_a_websocket_upgrade_is_tunnelled_both_ways(proxied):
    port, _ = proxied
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(
            f"GET /pty/abc/connect?ticket=t HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: x\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            head += s.recv(1024)
        assert head.startswith(b"HTTP/1.1 101")
        s.sendall(b"ls -la")
        assert s.recv(1024) == b"LS -LA"
    assert FakeOpenCode.seen[-1]["headers"]["Upgrade"] == "websocket"


def test_a_cross_site_websocket_is_refused(proxied):
    """Cross-site WebSocket hijacking: browsers send Origin on every WebSocket handshake."""
    port, _ = proxied
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(
            f"GET /pty/abc/connect HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://attacker.example\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n".encode()
        )
        assert s.recv(1024).startswith(b"HTTP/1.0 403")
    assert FakeOpenCode.seen == []


def test_the_proxy_only_listens_on_loopback(proxied):
    port, _ = proxied
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.connect(("192.0.2.1", 9))  # no packet is sent; this picks the LAN interface
    lan = probe.getsockname()[0]
    probe.close()
    if lan.startswith("127."):
        pytest.skip("no LAN interface")
    with socket.socket() as s:
        s.settimeout(1)
        assert s.connect_ex((lan, port)) != 0
