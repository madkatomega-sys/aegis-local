import pytest
import yaml

from aegis_router.routing import RouterConfig, RoutingError, prepare_upstream_body, select_route


@pytest.fixture
def config(repo_root) -> RouterConfig:
    return RouterConfig.from_dict(yaml.safe_load((repo_root / "config" / "router.yaml").read_text(encoding="utf-8")))


def body(model: str, text: str = "hello", **extra) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": text}], **extra}


def test_alias_routes_to_its_tier(config):
    assert select_route(config, body("aegis-reasoner"), {}).tier.name == "tier1"
    assert select_route(config, body("aegis-fast"), {}).tier.name == "tier0"


def test_task_header_routes_when_model_is_not_an_alias(config):
    route = select_route(config, body("anything"), {"x-aegis-task": "summarize"})
    assert route.tier.name == "tier0" and route.reason == "task:summarize"


def test_default_tier(config):
    assert select_route(config, body("anything"), {}).tier.name == config.default_tier


def test_large_prompt_escalates_fast_alias(config):
    big = "word " * (config.tier0_max_prompt_tokens * config.chars_per_token // 5 + 100)
    route = select_route(config, body("aegis-fast", big), {})
    assert route.tier.name == "tier1"
    assert "escalated" in route.reason


def test_extractor_never_escalates(config):
    big = "word " * (config.tier0_max_prompt_tokens * config.chars_per_token // 5 + 100)
    route = select_route(config, body("aegis-extractor", big), {})
    assert route.tier.name == "tier0"


def test_extraction_schema_is_bounded(config):
    """Unbounded fields let constrained decoding run into the token cap and return broken JSON."""
    schema = config.schemas["extraction"]
    worst_chars = schema["properties"]["summary"]["maxLength"]
    strings = 1
    for name in ("facts", "requested_actions", "verbatim_snippets"):
        field = schema["properties"][name]
        worst_chars += field["maxItems"] * field["items"]["maxLength"]
        strings += field["maxItems"]
    keys = len(schema["properties"])
    # A guarantee, not an estimate: every token is at least one character, and each string or key
    # adds at most 60 characters of quotes, commas, brackets, and grammar-bounded whitespace.
    worst_tokens = worst_chars + 60 * (strings + keys) + 100
    assert worst_tokens <= config.tiers["tier0"].max_output_tokens
    assert worst_tokens + 4000 <= config.tiers["tier0"].max_context_tokens  # room for the input too


def test_extractor_rejects_tools(config):
    with pytest.raises(RoutingError):
        select_route(config, body("aegis-extractor", tools=[{"type": "function"}]), {})


def test_extractor_schema_is_forced(config):
    request = body("aegis-extractor", response_format={"type": "text"})
    route = select_route(config, request, {})
    upstream = prepare_upstream_body(request, route)
    assert upstream["response_format"]["type"] == "json_schema"
    assert upstream["response_format"]["json_schema"]["name"] == "extraction"


def test_extractor_turns_thinking_off(config):
    """Reasoning before the grammar applies would count against the cap (25/100 failures in validation)."""
    request = body("aegis-extractor", chat_template_kwargs={"other": 1, "enable_thinking": True})
    upstream = prepare_upstream_body(request, select_route(config, request, {}))
    assert upstream["chat_template_kwargs"] == {"other": 1, "enable_thinking": False}


def test_other_aliases_leave_thinking_alone(config):
    for alias in ("aegis-reasoner", "aegis-fast"):
        request = body(alias)
        assert "chat_template_kwargs" not in prepare_upstream_body(request, select_route(config, request, {}))


def test_max_tokens_is_clamped(config):
    route = select_route(config, body("aegis-fast", max_tokens=10**6), {})
    assert route.max_tokens == config.tiers["tier0"].max_output_tokens
    upstream = prepare_upstream_body(
        body("aegis-fast", max_completion_tokens=5),
        select_route(config, body("aegis-fast", max_completion_tokens=5), {}),
    )
    assert upstream["max_tokens"] == 5 and "max_completion_tokens" not in upstream


def test_oversized_prompt_is_rejected(config):
    huge = "x" * (config.tiers["tier1"].max_context_tokens * config.chars_per_token + 10)
    with pytest.raises(RoutingError) as err:
        select_route(config, body("aegis-reasoner", huge), {})
    assert err.value.status == 413


def test_config_rejects_unknown_tier():
    with pytest.raises(ValueError):
        RouterConfig.from_dict(
            {
                "default_tier": "tier9",
                "tiers": {"tier0": {"upstream_env": "X", "max_output_tokens": 1, "max_context_tokens": 1}},
            }
        )
