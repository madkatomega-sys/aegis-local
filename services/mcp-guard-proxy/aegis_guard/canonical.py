"""Canonical forms and hashes for tool calls.

Two calls are "the same" for loop detection when they name the same tool and their arguments are
equal after canonicalization: keys sorted, strings Unicode-normalized and stripped, and floats
that are whole numbers written as integers. Formatting differences a model might introduce
(key order, trailing spaces) must not hide a repeat.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def call_digest(tool: str, arguments: Any) -> str:
    payload = f"{tool}\n{canonical_json(arguments or {})}".encode()
    return hashlib.sha256(payload).hexdigest()


def content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
