"""Chat tags: which OpenCode chat a tool call belongs to.

OpenCode keeps one MCP connection to the guard for as long as its server runs, so every chat in the
GUI would otherwise share one guard session: one breaker, one call budget, one taint flag. The Aegis
OpenCode plugin (`.opencode/plugins/aegis-chat.ts`) adds a `_aegis_chat` argument to each guard
tool call: the chat's root session ID and an HMAC of it keyed with the client token. The model
never sees the client token, so it can't forge a tag to move a call into another chat's (cleaner)
state.

A call without a tag uses the connection's own session, as before. That is the stricter of the two:
everything the connection does counts against one breaker and one taint flag. A tag that doesn't
verify is refused.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

TAG_ARG = "_aegis_chat"
_CHAT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def sign(chat: str, token: str) -> str:
    """The tag the plugin sends for `chat`: "<chat>.<hex HMAC-SHA256 of 'aegis-chat:<chat>'>"."""
    mac = hmac.new(token.encode(), f"aegis-chat:{chat}".encode(), hashlib.sha256).hexdigest()
    return f"{chat}.{mac}"


def split(params: dict[str, Any], token: str) -> tuple[dict[str, Any], str | None, str | None]:
    """Take the chat tag out of a tools/call's arguments.

    Returns (params without the tag, the chat ID or None, a problem or None). The tag is always
    removed, so it never reaches the call digest or the upstream worker."""
    arguments = params.get("arguments")
    if not isinstance(arguments, dict) or TAG_ARG not in arguments:
        return params, None, None
    arguments = dict(arguments)
    tag = arguments.pop(TAG_ARG)
    params = {**params, "arguments": arguments}
    if not isinstance(tag, str):
        return params, None, "the chat tag is malformed"
    chat, _, _mac = tag.rpartition(".")
    if not _CHAT_ID.fullmatch(chat) or not hmac.compare_digest(tag, sign(chat, token)):
        return params, None, "the chat tag isn't signed by the Aegis plugin"
    return params, chat, None
