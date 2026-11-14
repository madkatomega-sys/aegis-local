"""A stand-in for llama-server, for running the container stack without a model.

Standard library only. Answers /health, /v1/models, and /v1/chat/completions (plain and
streaming). When the request carries a JSON schema (the extractor alias), it returns a valid
extraction so quarantine can be exercised end to end. It never follows instructions in its
input; it only reports whether the input contained some.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = int(os.environ.get("STUB_PORT", "8000"))


def extraction(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    asks = [line for line in lines if "IGNORE" in line.upper() or "INSTRUCTION" in line.upper()]
    return {
        "summary": "Stub extraction of untrusted content.",
        "facts": lines[2:4],
        "requested_actions": ["The content asks the reader to act on embedded instructions."] if asks else [],
        "verbatim_snippets": [],
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": "stub", "object": "model"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        last = str((body.get("messages") or [{}])[-1].get("content", ""))
        if (body.get("response_format") or {}).get("type") == "json_schema":
            content = json.dumps(extraction(last))
        else:
            content = f"stub reply ({body.get('model')}, max_tokens={body.get('max_tokens')})"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in (content[: len(content) // 2], content[len(content) // 2 :]):
                event = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json(
            200,
            {
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            },
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
