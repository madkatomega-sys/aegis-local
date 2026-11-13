"""Tool policy and session taint.

Decides, for each tool call, whether it runs, is held for the user's approval, or is denied.
The inputs are the tool name, the policy file, and the session's taint state. No model is
involved, and anything that can't be evaluated is denied.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Action(StrEnum):
    ALLOW = "allow"
    HOLD = "hold"
    DENY = "deny"


class Approval(StrEnum):
    ALWAYS = "always"
    WHEN_TAINTED = "when_tainted"
    NEVER = "never"


class Trust(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


@dataclass(frozen=True)
class ToolRule:
    match: str
    mutating: bool = False
    deny: bool = False
    approval: Approval | None = None

    def matches(self, tool: str) -> bool:
        return fnmatch.fnmatchcase(tool, self.match)


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    rule: str | None = None


@dataclass
class SessionTaint:
    """High-water mark: once tainted, a session stays tainted."""

    tainted: bool = False
    sources: set[str] = field(default_factory=set)

    def mark(self, source: str) -> None:
        self.tainted = True
        self.sources.add(source)


@dataclass(frozen=True)
class TaintConfig:
    quarantine: bool = True
    extractor_alias: str = "aegis-extractor"
    max_verbatim_chars: int = 400
    max_input_chars: int = 40000
    on_extractor_failure: str = "deny"


@dataclass(frozen=True)
class Policy:
    rules: tuple[ToolRule, ...]
    default_for_mutating: Approval
    taint: TaintConfig

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Policy:
        rules = []
        for entry in raw.get("tools", []):
            approval = entry.get("approval")
            rules.append(
                ToolRule(
                    match=str(entry["match"]),
                    mutating=bool(entry.get("mutating", False)),
                    deny=bool(entry.get("deny", False)),
                    approval=Approval(approval) if approval else None,
                )
            )
        approvals = raw.get("approvals", {})
        taint = raw.get("taint", {})
        on_failure = str(taint.get("on_extractor_failure", "deny"))
        if on_failure not in {"deny", "raw_with_warning"}:
            raise ValueError(f"unknown on_extractor_failure: {on_failure}")
        return cls(
            rules=tuple(rules),
            default_for_mutating=Approval(approvals.get("default_for_mutating", "when_tainted")),
            taint=TaintConfig(
                quarantine=bool(taint.get("quarantine", True)),
                extractor_alias=str(taint.get("extractor_alias", "aegis-extractor")),
                max_verbatim_chars=int(taint.get("max_verbatim_chars", 400)),
                max_input_chars=int(taint.get("max_input_chars", 40000)),
                on_extractor_failure=on_failure,
            ),
        )

    def rule_for(self, tool: str) -> ToolRule | None:
        for rule in self.rules:
            if rule.matches(tool):
                return rule
        return None

    def is_visible(self, tool: str) -> bool:
        """Tools with no rule, or a deny rule, are hidden from tools/list."""
        rule = self.rule_for(tool)
        return rule is not None and not rule.deny

    def decide(self, tool: str, session: SessionTaint) -> Decision:
        rule = self.rule_for(tool)
        if rule is None:
            return Decision(Action.DENY, f"no policy rule matches {tool}")
        if rule.deny:
            return Decision(Action.DENY, f"{tool} is denied by policy", rule.match)
        if not rule.mutating:
            return Decision(Action.ALLOW, "read-only tool", rule.match)

        approval = rule.approval or self.default_for_mutating
        if approval is Approval.ALWAYS:
            return Decision(Action.HOLD, f"{tool} always requires approval", rule.match)
        if approval is Approval.WHEN_TAINTED and session.tainted:
            sources = ", ".join(sorted(session.sources)) or "unknown"
            return Decision(
                Action.HOLD,
                f"{tool} changes state and this session has read untrusted content from: {sources}",
                rule.match,
            )
        return Decision(Action.ALLOW, "mutating tool allowed in an untainted session", rule.match)
