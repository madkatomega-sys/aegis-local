"""Browser-interface check for a release: `aegis.py opencode --web` with the live stack.

Starts the launcher's OpenCode web server and drives it through the HTTP API the browser page
uses, then checks what a user would see and what the guard recorded:
- the server listens on loopback only, and wants its password;
- the origin check in front of it refuses a cross-site request that carries the password (as a
  signed-in browser would send it), a rebound host name, and a cross-site WebSocket, and OpenCode's
  own port wants the password;
- two Builder chats: the first fixes the sample project, the second runs its tests;
- the guard ledger holds each chat's calls under its own session;
- the chat titles are short titles, not reasoning;
- the status plugin raised a toast in each chat.

Run from the repository root with the stack up (`python aegis.py up`):

    python docs/validation/web_check.py --opencode <path to opencode 1.x> \
        --sample-repo <small git repo with a failing test> --out <transcript>

Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "smoke"))
sys.path.insert(0, str(ROOT))

import smoke  # noqa: E402

import aegis  # noqa: E402

PY = sys.executable
OUT: list[str] = []
FIX = "The tests in the sandbox workspace fail. Find the bug, fix it, and show me that the tests pass."
RERUN = "Run the tests in the sandbox workspace and tell me whether they pass."
LEDGER = """
import json, sqlite3, sys
db = sqlite3.connect('/data/ledger.sqlite')
if sys.argv[1] == 'max':
    print(db.execute('select coalesce(max(id), 0) from events').fetchone()[0])
else:
    query = 'select id, session, tool, decision, ok from events where id > ? order by id'
    rows = db.execute(query, (int(sys.argv[1]),))
    print(json.dumps([dict(zip(('id', 'session', 'tool', 'decision', 'ok'), r)) for r in rows]))
"""


def log(text: str = "") -> None:
    print(text, flush=True)
    OUT.append(text)


class Web:
    def __init__(self, port: int, password: str) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.auth = "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()

    def raw(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None):
        """Returns (status, headers, body) without raising on HTTP errors."""
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (loopback only)
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def api(self, method: str, path: str, body: dict | None = None, timeout: float = 60):
        req = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={"Authorization": self.auth, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (loopback only)
            return json.loads(resp.read() or b"null")


def ledger(arg: str) -> str:
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", "mcp-guard-proxy", "python", "-c", LEDGER, arg],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def lan_address() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.connect(("192.0.2.1", 9))  # no packet is sent; this only picks the LAN interface
    address = probe.getsockname()[0]
    probe.close()
    return address


def listen_for_toasts(web: Web, toasts: list[dict], stop: threading.Event) -> None:
    req = urllib.request.Request(web.base + "/event", headers={"Authorization": web.auth})
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:  # noqa: S310 (loopback only)
            for raw in resp:
                if stop.is_set():
                    return
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data:"):
                    try:
                        event = json.loads(line[5:])
                    except ValueError:
                        continue
                    if event.get("type") == "tui.toast.show":
                        toasts.append(event.get("properties") or {})
    except OSError:
        return


def chat(web: Web, text: str) -> tuple[str, list[dict], float]:
    session = web.api("POST", "/session", {})
    started = time.monotonic()
    web.api(
        "POST",
        f"/session/{session['id']}/message",
        {"agent": "builder", "parts": [{"type": "text", "text": text}]},
        900,
    )
    took = time.monotonic() - started
    parts = [p for m in web.api("GET", f"/session/{session['id']}/message") for p in m.get("parts", [])]
    tools = [p for p in parts if p.get("type") == "tool"]
    return session["id"], tools, took


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--opencode", required=True, help="path to the OpenCode 1.x binary")
    parser.add_argument("--sample-repo", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=4097)
    args = parser.parse_args()
    smoke.print = lambda *a, **k: log(" ".join(str(x) for x in a))  # route check lines into the transcript

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # OpenCode's banner isn't cp1252
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout
    log(f"# Browser interface check, commit {commit.strip()}")
    env = {**os.environ, "PATH": str(Path(args.opencode).resolve().parent) + os.pathsep + os.environ["PATH"]}
    version = subprocess.run([args.opencode, "--version"], capture_output=True, text=True).stdout.strip()
    log(f"OpenCode {version}")

    sample = Path(args.sample_repo).resolve()
    log(f"\n$ python aegis.py opencode --web <sample repo> --port {args.port} --no-browser")
    server_log = aegis.STATE / "web-check-server.log"
    server_log.parent.mkdir(parents=True, exist_ok=True)
    password = ""
    with server_log.open("w", encoding="utf-8") as sink:
        server = subprocess.Popen(  # noqa: S603
            [PY, "aegis.py", "opencode", "--web", str(sample), "--port", str(args.port), "--no-browser"],
            cwd=ROOT,
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    try:
        for _ in range(60):
            if aegis.port_in_use("127.0.0.1", args.port):
                break
            time.sleep(1)
        password = aegis.read_env_file(aegis.TOKENS)["OPENCODE_SERVER_PASSWORD"]
        web = Web(args.port, password)

        log("\n## Reachability and sign-in")
        lan = lan_address()
        with socket.socket() as s:
            s.settimeout(2)
            on_lan = s.connect_ex((lan, args.port)) == 0
        smoke.check(f"the server doesn't answer on the LAN address {lan}", not on_lan)
        status, _, _ = web.raw("GET", "/agent")
        smoke.check("no password: refused", status == 401, str(status))
        wrong = "Basic " + base64.b64encode(b"opencode:not-the-password").decode()
        status, _, _ = web.raw("GET", "/agent", headers={"Authorization": wrong})
        smoke.check("wrong password: refused", status == 401, str(status))
        status, _, body = web.raw("GET", "/agent", headers={"Authorization": web.auth})
        agents = [a.get("name") for a in json.loads(body)] if status == 200 else []
        smoke.check("right password: the agents are listed", status == 200 and "builder" in agents, str(agents))
        providers = [p.get("id") for p in web.api("GET", "/config/providers").get("providers", [])]
        smoke.check("only the Aegis provider is offered", providers == ["aegis"], str(providers))

        log("\n## A cross-site request that carries the password, as a signed-in browser would send it")
        evil = {"Origin": "http://attacker.example", "Authorization": web.auth}
        status, headers, _ = web.raw("OPTIONS", "/session", headers={**evil, "Access-Control-Request-Method": "POST"})
        allow = {k.lower(): v for k, v in headers.items()}.get("access-control-allow-origin", "(none)")
        log(f"  preflight from another origin: HTTP {status}, Access-Control-Allow-Origin: {allow}")
        smoke.check(
            "other origins aren't allowed to read replies", allow not in ("*", "http://attacker.example"), allow
        )
        before = len(web.api("GET", "/session"))
        status, _, _ = web.raw("POST", "/session", b"{}", {**evil, "Content-Type": "text/plain"})
        created = len(web.api("GET", "/session")) - before
        log(f"  a 'simple' cross-site POST /session (text/plain): HTTP {status}, sessions created: {created}")
        smoke.check("the cross-site POST is refused, and no session is created", status == 403 and created == 0)
        status, _, _ = web.raw("GET", "/session", headers={"Host": "evil.example:4097", "Authorization": web.auth})
        smoke.check("a request under another host name (DNS rebinding) is refused", status == 403, str(status))
        ws = {
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": "eA==",
            "Sec-WebSocket-Version": "13",
        }
        status, _, _ = web.raw("GET", "/pty", headers={**evil, **ws})
        smoke.check("a cross-site WebSocket is refused", status == 403, str(status))
        internal = re.search(r"OpenCode itself listens on 127\.0\.0\.1:(\d+)", server_log.read_text(encoding="utf-8"))
        smoke.check("the launcher says where OpenCode itself listens", bool(internal))
        if internal:
            direct = Web(int(internal.group(1)), password)
            status, _, _ = direct.raw("POST", "/session", b"{}", {"Origin": "http://attacker.example"})
            log(f"  straight to OpenCode's own port {internal.group(1)}, without the password: HTTP {status}")
            smoke.check(
                "OpenCode's own port wants the password, which the browser only holds for the proxy", status == 401
            )

        log("\n## Two Builder chats")
        toasts: list[dict] = []
        stop = threading.Event()
        threading.Thread(target=listen_for_toasts, args=(web, toasts, stop), daemon=True).start()
        subprocess.run([PY, "aegis.py", "workspace", "load", str(sample)], cwd=ROOT, check=True, capture_output=True)
        first_event = int(ledger("max"))
        results = []
        for text in (FIX, RERUN):
            seen = len(toasts)
            sid, tools, took = chat(web, text)
            names = [t.get("tool") for t in tools]
            checks = [t for t in tools if t.get("tool") == "aegis_code__run_check"]
            last = (checks[-1].get("state") or {}).get("output", "") if checks else ""
            log(f"  chat {sid}: {took:.0f} s, tools: {names}")
            results.append((sid, names, last, toasts[seen:]))
        time.sleep(20)  # titles are written in the background
        stop.set()

        (fix_sid, fix_tools, fix_last, fix_toasts), (rerun_sid, rerun_tools, rerun_last, rerun_toasts) = results
        edited = any(t in fix_tools for t in ("aegis_code__replace_in_file", "aegis_code__apply_patch"))
        smoke.check("chat 1 edited the sandbox", edited, str(fix_tools))
        smoke.check("chat 1's last test run passed", "exit code 0" in fix_last, fix_last[:300])
        smoke.check("chat 2 ran the tests and they passed", "exit code 0" in rerun_last, rerun_last[:300])

        rows = json.loads(ledger(str(first_event)))
        sessions = {r["session"] for r in rows}
        log(f"\n## Guard ledger: {len(rows)} events after id {first_event}")
        for r in rows:
            log(f"  {r['id']} {r['session'][:8]}…{r['session'][-12:]} {r['tool']} {r['decision']} ok={r['ok']}")
        chats = {s.partition(":")[2] for s in sessions}
        connections = {s.partition(":")[0] for s in sessions}
        smoke.check("every guard call carried a chat tag", all(":" in s for s in sessions), str(sessions))
        smoke.check("each chat has its own guard session", chats == {fix_sid, rerun_sid}, str(chats))
        smoke.check("both chats share one MCP connection", len(connections) == 1, str(connections))
        smoke.check("no call was denied, held, or tripped", all(r["decision"] == "allow" for r in rows))

        log("\n## Titles and status")
        for n, sid in enumerate((fix_sid, rerun_sid), 1):
            title = web.api("GET", f"/session/{sid}").get("title", "")
            log(f"  chat {n} title: {title!r}")
            thinking = title.lower().startswith(("okay", "<think", "the user", "let me", "alright"))
            smoke.check(f"chat {n}'s title is a title", 0 < len(title) <= 80 and not thinking, title)
        for n, found in enumerate((fix_toasts, rerun_toasts), 1):
            messages = [t.get("message", "") for t in found]
            log(f"  chat {n} toasts: {messages}")
            smoke.check(f"the status plugin spoke in chat {n}", any("Aegis" in m for m in messages), str(messages))
    finally:
        # OpenCode runs as the launcher's child: stop the whole tree, as Ctrl+C in its window would.
        if aegis.os_key() == "windows":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)], capture_output=True, check=False)
        else:
            server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
        time.sleep(3)
        output = server_log.read_text(encoding="utf-8", errors="replace")
        if password:
            output = output.replace(password, "<password>")
        log("\n## Launcher output\n" + output.strip())
        server_log.unlink(missing_ok=True)
        smoke.check("the server is gone after stopping it", not aegis.port_in_use("127.0.0.1", args.port))

    log(f"\n{len(smoke.FAILURES)} failure(s): {smoke.FAILURES}" if smoke.FAILURES else "\nall browser checks passed")
    Path(args.out).write_text("\n".join(OUT) + "\n", encoding="utf-8")
    return 1 if smoke.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
