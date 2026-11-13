import json

import pytest

import aegis


@pytest.mark.parametrize("name", aegis.list_profiles())
def test_every_profile_is_valid(name):
    assert aegis.validate_profile(aegis.load_profile(name)) == []


def test_profiles_exist():
    assert "cpu-only" in aegis.list_profiles()
    assert len(aegis.list_profiles()) >= 8


def test_validate_catches_port_collision():
    profile = aegis.load_profile("cpu-only")
    profile["tiers"]["tier1"]["port"] = profile["tiers"]["tier0"]["port"]
    assert any("used twice" in p for p in aegis.validate_profile(profile))


def test_validate_catches_guard_port():
    profile = aegis.load_profile("cpu-only")
    profile["tiers"]["tier0"]["port"] = 4100
    assert any("collides" in p for p in aegis.validate_profile(profile))


def test_read_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\nA=1\nB="two"  # trailing\n\nC=\n', encoding="utf-8")
    assert aegis.read_env_file(env) == {"A": "1", "B": "two", "C": ""}


def test_opencode_config_points_at_loopback(repo_root):
    config = json.loads((repo_root / "config" / "opencode.json").read_text(encoding="utf-8"))
    assert config["provider"]["aegis"]["options"]["baseURL"].startswith("http://127.0.0.1:")
    assert config["mcp"]["aegis"]["url"].startswith("http://127.0.0.1:")
    # Host-side editing, shell, and web fetches are off: all actions go through the guard.
    assert config["permission"] == {"edit": "deny", "bash": "deny", "webfetch": "deny"}
    for agent in config["agent"].values():
        prompt = agent["prompt"].removeprefix("{file:").removesuffix("}")
        assert (repo_root / "config" / prompt).is_file()


def test_compose_publishes_only_to_loopback(repo_root):
    import yaml

    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port}"
    for name in ("code-worker", "math-physics-mcp", "writing-auditor"):
        service = compose["services"][name]
        assert "ports" not in service
        for network in service["networks"]:
            assert compose["networks"][network].get("internal") is True
