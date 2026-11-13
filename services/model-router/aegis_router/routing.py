"""Deterministic tier selection for the model router.

This module has no web or network dependencies so it can be unit-tested directly. Given a
parsed router config and an OpenAI-style chat request, it decides which tier serves the request
and what limits apply, or rejects the request with a reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class RoutingError(ValueError):
    """The request can't be routed. The message is safe to return to the client."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Tier:
    name: str
    upstream_env: str
    max_output_tokens: int
    max_context_tokens: int


@dataclass(frozen=True)
class Alias:
    name: str
    tier: str
    escalate: bool = True
    tools_forbidden: bool = False
    response_schema: str | None = None


@dataclass(frozen=True)
class RouterConfig:
    default_tier: str
    tiers: dict[str, Tier]
    aliases: dict[str, Alias]
    tasks: dict[str, str]
    tier0_max_prompt_tokens: int
    chars_per_token: int
    schemas: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RouterConfig:
        tiers = {
            name: Tier(
                name=name,
                upstream_env=spec["upstream_env"],
                max_output_tokens=int(spec["max_output_tokens"]),
                max_context_tokens=int(spec["max_context_tokens"]),
            )
            for name, spec in raw["tiers"].items()
        }
        aliases = {
            name: Alias(
                name=name,
                tier=spec["tier"],
                escalate=bool(spec.get("escalate", True)),
                tools_forbidden=spec.get("tools") == "forbidden",
                response_schema=spec.get("response_schema"),
            )
            for name, spec in raw.get("aliases", {}).items()
        }
        tasks = {str(k): str(v) for k, v in raw.get("tasks", {}).items()}
        default_tier = raw["default_tier"]

        referenced = {default_tier, *tasks.values(), *(a.tier for a in aliases.values())}
        unknown = referenced - tiers.keys()
        if unknown:
            raise ValueError(f"router config references unknown tiers: {sorted(unknown)}")
        schemas = raw.get("schemas", {})
        for alias in aliases.values():
            if alias.response_schema and alias.response_schema not in schemas:
                raise ValueError(f"alias {alias.name} references unknown schema {alias.response_schema}")

        return cls(
            default_tier=default_tier,
            tiers=tiers,
            aliases=aliases,
            tasks=tasks,
            tier0_max_prompt_tokens=int(raw.get("escalation", {}).get("tier0_max_prompt_tokens", 12000)),
            chars_per_token=max(1, int(raw.get("chars_per_token", 4))),
            schemas=schemas,
        )


@dataclass(frozen=True)
class Route:
    tier: Tier
    alias: Alias | None
    reason: str
    prompt_tokens_estimate: int
    max_tokens: int
    response_format: dict[str, Any] | None


def estimate_prompt_tokens(body: dict[str, Any], chars_per_token: int) -> int:
    """Cheap, deterministic estimate. Exact counts aren't needed to pick a tier."""
    chars = 0
    for message in body.get("messages", []) or []:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chars += len(part["text"])
    for tool in body.get("tools", []) or []:
        chars += len(str(tool))
    return chars // chars_per_token + 1


def select_route(config: RouterConfig, body: dict[str, Any], headers: dict[str, str]) -> Route:
    model = str(body.get("model") or "")
    task = (headers.get("x-aegis-task") or "").strip().lower()
    alias = config.aliases.get(model)

    if alias is not None:
        tier_name, reason = alias.tier, f"alias:{alias.name}"
    elif task and task in config.tasks:
        tier_name, reason = config.tasks[task], f"task:{task}"
    else:
        tier_name, reason = config.default_tier, "default"

    if alias is not None and alias.tools_forbidden and body.get("tools"):
        raise RoutingError(f"model alias '{alias.name}' does not accept tools")

    estimate = estimate_prompt_tokens(body, config.chars_per_token)
    can_escalate = alias.escalate if alias is not None else True
    if tier_name == "tier0" and estimate > config.tier0_max_prompt_tokens and can_escalate and "tier1" in config.tiers:
        tier_name, reason = "tier1", f"{reason}+escalated:prompt_tokens>{config.tier0_max_prompt_tokens}"

    tier = config.tiers[tier_name]
    if estimate > tier.max_context_tokens:
        raise RoutingError(
            f"prompt is about {estimate} tokens; {tier.name} accepts at most {tier.max_context_tokens}",
            status=413,
        )

    requested = body.get("max_tokens") or body.get("max_completion_tokens")
    try:
        requested_int = int(requested) if requested is not None else tier.max_output_tokens
    except (TypeError, ValueError) as exc:
        raise RoutingError("max_tokens must be an integer") from exc
    max_tokens = max(1, min(requested_int, tier.max_output_tokens))

    response_format = None
    if alias is not None and alias.response_schema:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": alias.response_schema,
                "strict": True,
                "schema": config.schemas[alias.response_schema],
            },
        }

    return Route(
        tier=tier,
        alias=alias,
        reason=reason,
        prompt_tokens_estimate=estimate,
        max_tokens=max_tokens,
        response_format=response_format,
    )


def prepare_upstream_body(body: dict[str, Any], route: Route) -> dict[str, Any]:
    """Return the request body to send to the tier, with caps and schema applied."""
    out = dict(body)
    out["model"] = route.tier.name
    out.pop("max_completion_tokens", None)
    out["max_tokens"] = route.max_tokens
    if route.response_format is not None:
        # The alias schema always wins over anything the client asked for.
        out["response_format"] = route.response_format
    return out
