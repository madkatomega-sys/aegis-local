"""SQLite audit log and approval queue.

Every tool call the guard sees is recorded with its decision and a hash of its result. Calls
held for approval wait here until the user approves or denies them out of band
(`python aegis.py approve <id>`). An approval is good for exactly one execution of exactly the
call that was held.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session TEXT NOT NULL,
    tool TEXT NOT NULL,
    digest TEXT NOT NULL,
    decision TEXT NOT NULL,
    rule TEXT,
    reason TEXT,
    ok INTEGER,
    result_digest TEXT,
    tainted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    created REAL NOT NULL,
    decided REAL,
    session TEXT NOT NULL,
    tool TEXT NOT NULL,
    digest TEXT NOT NULL,
    arguments TEXT NOT NULL,
    reason TEXT NOT NULL,
    taint_sources TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'consumed'))
);
CREATE INDEX IF NOT EXISTS approvals_lookup ON approvals (session, digest, status);
"""


@dataclass(frozen=True)
class PendingApproval:
    id: str
    session: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    taint_sources: list[str]
    status: str
    created: float


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def log(
        self,
        *,
        session: str,
        tool: str,
        digest: str,
        decision: str,
        rule: str | None = None,
        reason: str | None = None,
        ok: bool | None = None,
        result_digest: str | None = None,
        tainted: bool = False,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO events (ts, session, tool, digest, decision, rule, reason, ok, result_digest, tainted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    session,
                    tool,
                    digest,
                    decision,
                    rule,
                    reason,
                    None if ok is None else int(ok),
                    result_digest,
                    int(tainted),
                ),
            )

    def request_approval(
        self, *, session: str, tool: str, digest: str, arguments: dict[str, Any], reason: str, taint_sources: list[str]
    ) -> str:
        """Return the id of a pending approval for this call, creating one if needed."""
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM approvals WHERE session = ? AND digest = ? AND status = 'pending'", (session, digest)
            ).fetchone()
            if row:
                return row[0]
            approval_id = uuid.uuid4().hex[:8]
            self._db.execute(
                "INSERT INTO approvals (id, created, session, tool, digest, arguments, reason, taint_sources, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (
                    approval_id,
                    time.time(),
                    session,
                    tool,
                    digest,
                    json.dumps(arguments, sort_keys=True),
                    reason,
                    json.dumps(sorted(taint_sources)),
                ),
            )
            return approval_id

    def consume_approval(self, session: str, digest: str) -> bool:
        """If this exact call was approved, mark the approval used and return True."""
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM approvals WHERE session = ? AND digest = ? AND status = 'approved' "
                "ORDER BY decided LIMIT 1",
                (session, digest),
            ).fetchone()
            if not row:
                return False
            self._db.execute("UPDATE approvals SET status = 'consumed' WHERE id = ?", (row[0],))
            return True

    def decide(self, approval_id: str, approve: bool) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE approvals SET status = ?, decided = ? WHERE id = ? AND status = 'pending'",
                ("approved" if approve else "denied", time.time(), approval_id),
            )
            return cur.rowcount == 1

    def pending(self) -> list[PendingApproval]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, session, tool, arguments, reason, taint_sources, status, created "
                "FROM approvals WHERE status = 'pending' ORDER BY created"
            ).fetchall()
        return [
            PendingApproval(
                id=r[0],
                session=r[1],
                tool=r[2],
                arguments=json.loads(r[3]),
                reason=r[4],
                taint_sources=json.loads(r[5]),
                status=r[6],
                created=r[7],
            )
            for r in rows
        ]
