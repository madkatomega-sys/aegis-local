"""Circuit breaker for agent tool loops.

A language model can't reliably tell from inside its own reasoning that it is stuck. This module
watches the sequence of tool calls from outside and trips when the pattern shows no progress:

- budget:       too many attempted calls in one session
- failures:     too many failing results in a row
- repeat:       the same call (tool + canonical args) too often within the window
- retry:        repeating a call that already failed
- oscillation:  a short cycle of calls repeating (A,B,A,B or A,B,C,A,B,C)
- near-miss:    a streak of failing calls to one tool with nearly identical arguments

Once tripped, a breaker stays tripped until the user resets the session. Everything here is
deterministic; the similarity measure is difflib's ratio on canonical argument strings.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from aegis_guard.canonical import call_digest, canonical_json

Similarity = Callable[[str, str], float]


def text_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


@dataclass(frozen=True)
class BreakerConfig:
    max_tool_calls: int = 80
    max_consecutive_failures: int = 5
    window: int = 12
    max_identical_calls: int = 3
    max_identical_failures: int = 1
    oscillation_periods: tuple[int, ...] = (2, 3)
    oscillation_repeats: int = 2
    near_miss_similarity: float = 0.92
    near_miss_streak: int = 3

    @classmethod
    def from_policy(cls, policy: dict[str, Any]) -> BreakerConfig:
        budgets = policy.get("budgets", {})
        breaker = policy.get("breaker", {})
        near = breaker.get("near_miss", {})
        return cls(
            max_tool_calls=int(budgets.get("max_tool_calls_per_session", cls.max_tool_calls)),
            max_consecutive_failures=int(budgets.get("max_consecutive_failures", cls.max_consecutive_failures)),
            window=int(breaker.get("window", cls.window)),
            max_identical_calls=int(breaker.get("max_identical_calls", cls.max_identical_calls)),
            max_identical_failures=int(breaker.get("max_identical_failures", cls.max_identical_failures)),
            oscillation_periods=tuple(int(p) for p in breaker.get("oscillation_periods", cls.oscillation_periods)),
            oscillation_repeats=int(breaker.get("oscillation_repeats", cls.oscillation_repeats)),
            near_miss_similarity=float(near.get("similarity", cls.near_miss_similarity)),
            near_miss_streak=int(near.get("failing_streak", cls.near_miss_streak)),
        )


@dataclass
class CallRecord:
    tool: str
    args: str
    digest: str
    ok: bool | None = None


@dataclass(frozen=True)
class BreakerDecision:
    allowed: bool
    rule: str | None = None
    reason: str | None = None
    digest: str | None = None


@dataclass
class CycleBreaker:
    config: BreakerConfig = field(default_factory=BreakerConfig)
    similarity: Similarity = text_similarity
    history: list[CallRecord] = field(default_factory=list)
    attempts: int = 0
    consecutive_failures: int = 0
    tripped: BreakerDecision | None = None

    def check(self, tool: str, arguments: Any) -> BreakerDecision:
        """Decide whether a call may run. Allowed calls are added to the history."""
        self.attempts += 1
        if self.tripped is not None:
            return self.tripped

        args = canonical_json(arguments or {})
        digest = call_digest(tool, arguments)
        decision = self._evaluate(tool, args, digest)
        if not decision.allowed:
            self.tripped = decision
            return decision

        self.history.append(CallRecord(tool=tool, args=args, digest=digest))
        return BreakerDecision(allowed=True, digest=digest)

    def record_result(self, digest: str, ok: bool) -> None:
        """Attach the outcome to the most recent pending call with this digest."""
        for record in reversed(self.history):
            if record.digest == digest and record.ok is None:
                record.ok = ok
                break
        self.consecutive_failures = 0 if ok else self.consecutive_failures + 1

    def note_attempt(self) -> None:
        """Count an attempt that never reached check() (held for approval, denied by policy)."""
        self.attempts += 1

    def reset(self) -> None:
        self.history.clear()
        self.attempts = 0
        self.consecutive_failures = 0
        self.tripped = None

    @property
    def remaining_budget(self) -> int:
        return max(0, self.config.max_tool_calls - self.attempts)

    # -- rules -----------------------------------------------------------------------------

    def _trip(self, rule: str, reason: str, digest: str) -> BreakerDecision:
        return BreakerDecision(allowed=False, rule=rule, reason=reason, digest=digest)

    def _evaluate(self, tool: str, args: str, digest: str) -> BreakerDecision:
        cfg = self.config
        if self.attempts > cfg.max_tool_calls:
            return self._trip("budget", f"session used its budget of {cfg.max_tool_calls} tool calls", digest)

        if self.consecutive_failures >= cfg.max_consecutive_failures:
            return self._trip(
                "failures",
                f"{self.consecutive_failures} tool calls failed in a row; stopping to avoid a retry spiral",
                digest,
            )

        window = self.history[-cfg.window :] if cfg.window > 0 else []
        same = [r for r in window if r.digest == digest]
        failed_same = sum(1 for r in same if r.ok is False)
        if failed_same >= cfg.max_identical_failures:
            return self._trip("retry", f"{tool} was already called with identical arguments and failed", digest)
        if len(same) + 1 > cfg.max_identical_calls:
            return self._trip(
                "repeat",
                f"{tool} called with identical arguments {len(same) + 1} times in the last {cfg.window} calls",
                digest,
            )

        sequence = [r.digest for r in window] + [digest]
        for period in cfg.oscillation_periods:
            if _is_oscillating(sequence, period, cfg.oscillation_repeats):
                return self._trip("oscillation", f"calls are cycling with period {period} without progress", digest)

        streak = cfg.near_miss_streak
        if streak >= 2 and len(window) >= streak - 1:
            previous = window[-(streak - 1) :]
            if all(r.tool == tool and r.ok is False for r in previous):
                chain = [r.args for r in previous] + [args]
                if all(
                    self.similarity(a, b) >= cfg.near_miss_similarity for a, b in zip(chain, chain[1:], strict=False)
                ):
                    return self._trip(
                        "near_miss", f"{streak} near-identical calls to {tool}, the earlier ones all failing", digest
                    )

        return BreakerDecision(allowed=True, digest=digest)


def _is_oscillating(sequence: Sequence[str], period: int, repeats: int) -> bool:
    span = period * repeats
    if period < 2 or len(sequence) < span:
        return False
    tail = sequence[-span:]
    pattern = tail[:period]
    if len(set(pattern)) < 2:
        return False  # a single repeated call is the "repeat" rule's job
    return all(tail[i] == pattern[i % period] for i in range(span))
