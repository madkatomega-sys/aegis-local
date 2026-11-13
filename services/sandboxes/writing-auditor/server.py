"""Writing auditor MCP worker: search a read-only notes vault and check citations."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

from vault import VaultIndex

VAULT = Path(os.environ.get("AEGIS_VAULT", "/vault"))
INDEX = Path(os.environ.get("AEGIS_INDEX", "/data/index.sqlite"))

mcp = FastMCP("writing-auditor", host="0.0.0.0", port=8000, stateless_http=True, json_response=True)
index = VaultIndex(INDEX)


@mcp.tool()
def index_vault() -> dict[str, int]:
    """Rebuild the search index from the vault. Run after the user's notes change."""
    if not VAULT.is_dir():
        raise ToolError("no vault is mounted")
    return index.rebuild(VAULT)


@mcp.tool()
def search(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Full-text search (BM25) over indexed notes. Returns chunk IDs to cite as [@chunk_id]."""
    return index.search(query, limit)


@mcp.tool()
def get_chunk(chunk_id: str) -> dict[str, str]:
    """Return the full text, source URI, and SHA-256 of a chunk."""
    chunk = index.get(chunk_id)
    if chunk is None:
        raise ToolError(f"no chunk {chunk_id}")
    return {
        "chunk_id": chunk.chunk_id,
        "uri": chunk.uri,
        "heading": chunk.heading,
        "sha256": chunk.sha256,
        "text": chunk.text,
    }


@mcp.tool()
def verify_quote(chunk_id: str, quote: str) -> dict[str, object]:
    """Check that a quotation appears exactly (after whitespace and quote-mark normalization) in a chunk."""
    return index.verify_quote(chunk_id, quote)


@mcp.tool()
def audit_draft(draft: str) -> dict[str, object]:
    """List sentences without a [@chunk_id] citation and citations that don't exist.
    Mark deliberate unsourced sentences with [unsourced]."""
    return index.audit_draft(draft)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
