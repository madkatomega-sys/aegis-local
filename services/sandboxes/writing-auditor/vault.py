"""Vault indexing and citation checks for the writing auditor.

Pure Python and SQLite (FTS5 gives BM25 ranking), no model involved. Notes are split into
chunks by heading and paragraph; every chunk gets a stable ID derived from its POSIX vault path
and a SHA-256 of its text, so a citation keeps meaning the same thing across machines and
breaks visibly if the source changes.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
MAX_CHUNK_CHARS = 1500
CITE = re.compile(r"\[@([a-z0-9]{8,64})\]")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    uri TEXT NOT NULL,
    heading TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, heading, text);
"""


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    uri: str
    heading: str
    sha256: str
    text: str


def normalize(text: str) -> str:
    """Normalization used when comparing quotes: Unicode NFKC, straight quotes, collapsed spaces."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def vault_uri(root: Path, path: Path) -> str:
    return "vault://" + str(PurePosixPath(*path.relative_to(root).parts))


def chunk_document(uri: str, text: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    heading = ""
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        buffer.clear()
        while body:
            piece, body = body[:MAX_CHUNK_CHARS], body[MAX_CHUNK_CHARS:]
            digest = hashlib.sha256(piece.encode("utf-8")).hexdigest()
            chunk_id = hashlib.sha256(f"{uri}\n{digest}".encode()).hexdigest()[:16]
            chunks.append(Chunk(chunk_id, uri, heading, digest, piece))

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip()
        elif not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()
    return chunks


class VaultIndex:
    def __init__(self, db_path: str | Path) -> None:
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.executescript(SCHEMA)

    def rebuild(self, root: Path) -> dict[str, int]:
        files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES)
        with self.db:
            self.db.execute("DELETE FROM chunks")
            self.db.execute("DELETE FROM chunks_fts")
            count = 0
            for path in files:
                uri = vault_uri(root, path)
                for chunk in chunk_document(uri, path.read_text(encoding="utf-8", errors="replace")):
                    self.db.execute(
                        "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?)",
                        (chunk.chunk_id, chunk.uri, chunk.heading, chunk.sha256, chunk.text),
                    )
                    self.db.execute(
                        "INSERT INTO chunks_fts (chunk_id, heading, text) VALUES (?, ?, ?)",
                        (chunk.chunk_id, chunk.heading, chunk.text),
                    )
                    count += 1
        return {"files": len(files), "chunks": count}

    def get(self, chunk_id: str) -> Chunk | None:
        row = self.db.execute(
            "SELECT chunk_id, uri, heading, sha256, text FROM chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        return Chunk(*row) if row else None

    def search(self, query: str, limit: int = 8) -> list[dict[str, str]]:
        terms = [t for t in re.findall(r"\w+", query) if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = self.db.execute(
            "SELECT c.chunk_id, c.uri, c.heading, snippet(chunks_fts, 2, '[', ']', ' ... ', 24) "
            "FROM chunks_fts JOIN chunks c USING (chunk_id) WHERE chunks_fts MATCH ? "
            "ORDER BY bm25(chunks_fts) LIMIT ?",
            (match, max(1, min(limit, 25))),
        ).fetchall()
        return [{"chunk_id": r[0], "uri": r[1], "heading": r[2], "snippet": r[3]} for r in rows]

    def verify_quote(self, chunk_id: str, quote: str) -> dict[str, object]:
        chunk = self.get(chunk_id)
        if chunk is None:
            return {"verified": False, "reason": f"no chunk {chunk_id}"}
        found = normalize(quote) in normalize(chunk.text)
        return {
            "verified": found,
            "uri": chunk.uri,
            "sha256": chunk.sha256,
            "reason": "exact match after normalization" if found else "quote not found in chunk",
        }

    def audit_draft(self, draft: str) -> dict[str, object]:
        """Report sentences without a citation and citations that point nowhere."""
        uncited: list[str] = []
        unknown: list[str] = []
        cited = 0
        for paragraph in draft.split("\n\n"):
            if paragraph.lstrip().startswith(("#", "```", ">")):
                continue
            for sentence in SENTENCE.split(paragraph.strip()):
                if not sentence.strip():
                    continue
                ids = CITE.findall(sentence)
                if not ids:
                    if "[unsourced]" not in sentence:
                        uncited.append(sentence.strip()[:200])
                    continue
                cited += 1
                unknown.extend(i for i in ids if self.get(i) is None)
        return {
            "cited_sentences": cited,
            "uncited_sentences": uncited,
            "unknown_citations": sorted(set(unknown)),
            "clean": not uncited and not unknown,
        }
