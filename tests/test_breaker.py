from aegis_guard.breaker import BreakerConfig, CycleBreaker
from aegis_guard.canonical import call_digest, canonical_json


def run(breaker: CycleBreaker, tool: str, args: dict, ok: bool = True):
    decision = breaker.check(tool, args)
    if decision.allowed:
        breaker.record_result(decision.digest, ok)
    return decision


def test_canonical_ignores_key_order_and_whitespace():
    a = {"path": " src/app.py ", "lines": [1, 2.0]}
    b = {"lines": [1, 2], "path": "src/app.py"}
    assert canonical_json(a) == canonical_json(b)
    assert call_digest("code__read_file", a) == call_digest("code__read_file", b)


def test_canonical_distinguishes_bool_from_int():
    assert call_digest("t", {"x": True}) != call_digest("t", {"x": 1})


def test_distinct_successful_calls_are_allowed():
    breaker = CycleBreaker()
    for i in range(10):
        assert run(breaker, "math__simplify", {"expression": f"x+{i}"}).allowed


def test_retrying_an_identical_failed_call_trips():
    breaker = CycleBreaker()
    assert run(breaker, "code__run_check", {"check": "pytest"}, ok=False).allowed
    decision = breaker.check("code__run_check", {"check": "pytest"})
    assert not decision.allowed
    assert decision.rule == "retry"


def test_identical_successful_calls_trip_after_limit():
    breaker = CycleBreaker(BreakerConfig(max_identical_calls=3, oscillation_repeats=99))
    for _ in range(3):
        assert run(breaker, "code__read_file", {"path": "a.py"}).allowed
    decision = breaker.check("code__read_file", {"path": "a.py"})
    assert decision.rule == "repeat"


def test_two_step_oscillation_trips():
    breaker = CycleBreaker()
    assert run(breaker, "code__read_file", {"path": "a.py"}).allowed
    assert run(breaker, "code__read_file", {"path": "b.py"}).allowed
    assert run(breaker, "code__read_file", {"path": "a.py"}).allowed
    decision = breaker.check("code__read_file", {"path": "b.py"})
    assert not decision.allowed
    assert decision.rule == "oscillation"


def test_three_step_oscillation_trips():
    breaker = CycleBreaker(BreakerConfig(max_identical_calls=5))
    calls = [("t", {"n": 1}), ("t", {"n": 2}), ("t", {"n": 3})] * 2
    decisions = [breaker.check(tool, args) for tool, args in calls]
    for d in decisions:
        if d.allowed:
            breaker.record_result(d.digest, True)
    assert all(d.allowed for d in decisions[:-1])
    assert decisions[-1].rule == "oscillation"


def test_near_miss_failing_streak_trips():
    breaker = CycleBreaker(BreakerConfig(near_miss_streak=3, near_miss_similarity=0.9))
    base = "SELECT name, email FROM users WHERE created_at > '2026-01-01' ORDER BY name"
    assert run(breaker, "db__query", {"sql": base + " LIMIT 10"}, ok=False).allowed
    assert run(breaker, "db__query", {"sql": base + " LIMIT 11"}, ok=False).allowed
    decision = breaker.check("db__query", {"sql": base + " LIMIT 12"})
    assert decision.rule == "near_miss"


def test_near_miss_does_not_trip_when_earlier_calls_succeeded():
    breaker = CycleBreaker(BreakerConfig(near_miss_streak=3, near_miss_similarity=0.9))
    base = "SELECT name, email FROM users WHERE created_at > '2026-01-01' ORDER BY name"
    assert run(breaker, "db__query", {"sql": base + " LIMIT 10"}, ok=True).allowed
    assert run(breaker, "db__query", {"sql": base + " LIMIT 11"}, ok=False).allowed
    assert breaker.check("db__query", {"sql": base + " LIMIT 12"}).allowed


def test_consecutive_failures_trip():
    breaker = CycleBreaker(BreakerConfig(max_consecutive_failures=3, near_miss_streak=99))
    for i in range(3):
        assert run(breaker, "code__run_check", {"check": "pytest", "path": f"t{i}.py"}, ok=False).allowed
    assert breaker.check("code__run_check", {"check": "ruff"}).rule == "failures"


def test_budget_counts_blocked_attempts():
    breaker = CycleBreaker(BreakerConfig(max_tool_calls=3))
    breaker.note_attempt()
    breaker.note_attempt()
    assert run(breaker, "t", {"n": 1}).allowed
    assert breaker.check("t", {"n": 2}).rule == "budget"


def test_trip_is_sticky_until_reset():
    breaker = CycleBreaker()
    run(breaker, "t", {"n": 1}, ok=False)
    assert not breaker.check("t", {"n": 1}).allowed
    assert not breaker.check("t", {"n": 999}).allowed
    breaker.reset()
    assert breaker.check("t", {"n": 999}).allowed


def test_config_from_policy_file(repo_root):
    import yaml

    raw = yaml.safe_load((repo_root / "config" / "policy.yaml").read_text(encoding="utf-8"))
    config = BreakerConfig.from_policy(raw)
    assert config.max_tool_calls == raw["budgets"]["max_tool_calls_per_session"]
    assert config.near_miss_similarity == raw["breaker"]["near_miss"]["similarity"]
