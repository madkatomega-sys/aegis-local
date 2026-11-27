import json
import subprocess

import pytest

import aegis
import aegis_web


@pytest.mark.parametrize("name", aegis.list_profiles())
def test_every_profile_is_valid(name):
    assert aegis.validate_profile(aegis.load_profile(name)) == []


@pytest.mark.parametrize("name", aegis.list_profiles())
def test_tier0_never_thinks(name):
    """tier0 serves the extractor; thinking would spend its token budget before the JSON starts."""
    args = aegis.load_profile(name)["tiers"]["tier0"]["extra_args"]
    assert args[args.index("--reasoning-budget") + 1] == "0"


def test_workspace_commands_trust_the_read_only_mount():
    assert "safe.directory" in aegis.SAFE_DIRECTORY and "--global" in aegis.SAFE_DIRECTORY


def test_opencode_loads_the_chat_tag_plugin():
    """Without OPENCODE_CONFIG_DIR, OpenCode doesn't load .opencode/plugins, and all chats share a guard session."""
    env = aegis.opencode_env("token")
    assert env["OPENCODE_CONFIG"].endswith("opencode.json")
    plugins = aegis.Path(env["OPENCODE_CONFIG_DIR"]) / "plugins"
    assert (plugins / "aegis-chat.ts").is_file()
    assert env["AEGIS_CLIENT_TOKEN"] == "token"


class Calls(list):
    """How opencode was started, plus `proxies` and `opened` (what the browser was pointed at)."""


@pytest.fixture
def fake_opencode(monkeypatch, tmp_path):
    """aegis.py with its state in tmp_path and an `opencode` that records how it was started."""
    monkeypatch.setattr(aegis, "STATE", tmp_path / ".aegis")
    monkeypatch.setattr(aegis, "TOKENS", tmp_path / ".aegis" / "tokens.env")
    monkeypatch.setattr(aegis.shutil, "which", lambda name: "opencode")
    version = {"value": "1.19.5"}
    calls = Calls()
    monkeypatch.setattr(aegis, "opencode_version", lambda binary: version["value"])

    def fake_call(command, env=None, cwd=None):
        calls.append({"command": command, "env": env, "cwd": cwd})
        return 0

    class FakeServer:
        def __init__(self, command, env=None, cwd=None):
            calls.append({"command": command, "env": env, "cwd": cwd})

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    class FakeProxy:
        def __init__(self, public, upstream):
            proxies.append((public, upstream))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            proxies.append("stopped")

    proxies: list = []
    opened: list = []
    monkeypatch.setattr(aegis.subprocess, "call", fake_call)
    monkeypatch.setattr(aegis.subprocess, "Popen", FakeServer)
    monkeypatch.setattr(aegis_web, "Proxy", FakeProxy)
    monkeypatch.setattr(aegis_web, "free_port", lambda: 51234)
    monkeypatch.setattr(aegis, "port_in_use", lambda host, port: True)
    monkeypatch.setattr(aegis.webbrowser, "open", opened.append)
    calls.proxies, calls.opened = proxies, opened
    return calls, version


def test_web_puts_opencode_behind_the_origin_check_on_loopback(fake_opencode, tmp_path, capsys):
    calls, _ = fake_opencode
    assert aegis.main(["opencode", "--web", str(tmp_path)]) == 0
    (started,) = calls
    # OpenCode serves on a spare loopback port; the user's port is the proxy in front of it.
    assert started["command"] == ["opencode", "serve", "--hostname", "127.0.0.1", "--port", "51234"]
    assert calls.proxies == [(4096, 51234), "stopped"]
    assert calls.opened == ["http://127.0.0.1:4096/"]  # the browser opens the proxy, never OpenCode's port
    assert started["cwd"] == tmp_path.resolve()
    env = started["env"]
    password = env["OPENCODE_SERVER_PASSWORD"]
    assert len(password) >= 20
    assert env["OPENCODE_CONFIG_DIR"].endswith(".opencode")
    # A private OpenCode home under .aegis, so no other OpenCode setup leaks in.
    assert all(env[f"XDG_{n}_HOME"].startswith(str(tmp_path / ".aegis" / "opencode-web")) for n in ("CONFIG", "DATA"))
    assert password in capsys.readouterr().out
    # The password is kept and reused.
    assert aegis.main(["opencode", "--web", str(tmp_path)]) == 0
    assert calls[1]["env"]["OPENCODE_SERVER_PASSWORD"] == password
    assert aegis.read_env_file(tmp_path / ".aegis" / "tokens.env")["OPENCODE_SERVER_PASSWORD"] == password


def test_web_takes_a_port(fake_opencode, tmp_path):
    calls, _ = fake_opencode
    aegis.main(["opencode", "--web", "--port", "4300", str(tmp_path)])
    assert calls.proxies[0] == (4300, 51234)
    assert calls.opened == ["http://127.0.0.1:4300/"]


def test_web_can_leave_the_browser_closed(fake_opencode, tmp_path):
    calls, _ = fake_opencode
    assert aegis.main(["opencode", "--web", "--no-browser", str(tmp_path)]) == 0
    assert calls.opened == []
    assert calls.proxies == [(4096, 51234), "stopped"]


@pytest.mark.parametrize("version", ["2.3.0", "", "unknown"])
def test_web_refuses_opencode_versions_it_has_not_been_checked_on(fake_opencode, tmp_path, version):
    calls, current = fake_opencode
    current["value"] = version
    with pytest.raises(SystemExit):
        aegis.main(["opencode", "--web", str(tmp_path)])
    assert calls == []


def test_without_web_the_terminal_ui_starts_as_before(fake_opencode, tmp_path):
    calls, _ = fake_opencode
    aegis.main(["opencode", str(tmp_path)])
    assert calls[0]["command"] == ["opencode", str(tmp_path)]
    assert "OPENCODE_SERVER_PASSWORD" not in calls[0]["env"] or calls[0]["env"]["OPENCODE_SERVER_PASSWORD"] == (
        aegis.os.environ.get("OPENCODE_SERVER_PASSWORD")
    )


@pytest.mark.parametrize("plugin", ["aegis-chat.ts", "aegis-hud.ts"])
def test_plugin_files_export_only_the_plugin(repo_root, plugin):
    """OpenCode calls every export of a plugin file as a plugin, so a helper export would be run as one."""
    source = (repo_root / ".opencode" / "plugins" / plugin).read_text(encoding="utf-8")
    exports = [line for line in source.splitlines() if line.startswith("export ")]
    assert len(exports) == 1 and ": Plugin = async" in exports[0]


def test_the_chat_plugin_signs_what_the_guard_verifies(repo_root):
    """The plugin and aegis_guard.chat must agree on the message they sign and the tag's shape."""
    source = (repo_root / ".opencode" / "plugins" / "aegis-chat.ts").read_text(encoding="utf-8")
    assert 'createHmac("sha256", TOKEN).update(`aegis-chat:${chat}`).digest("hex")' in source
    assert "output.args[TAG_ARG] = `${chat}.${mac}`" in source
    assert 'const TAG_ARG = "_aegis_chat"' in source


CRLF_DIFF = b"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\r\n+x = 2\r\n"


def test_workspace_export_keeps_crlf_lines(monkeypatch, tmp_path):
    """Text mode turned the diff's CRLF lines into LF, so a patch for a CRLF file no longer applied."""
    calls = []

    def fake_compose(runtime, *args, env, capture=False, text=True):
        calls.append(text)
        return subprocess.CompletedProcess(args, 0, stdout=CRLF_DIFF if not text else CRLF_DIFF.decode(), stderr=b"")

    monkeypatch.setattr(aegis, "compose", fake_compose)
    monkeypatch.setattr(aegis, "detect_runtime", lambda cfg: "docker")
    monkeypatch.setattr(aegis, "ROOT", tmp_path)
    assert aegis.workspace_diff("docker", {}) == CRLF_DIFF
    assert aegis.main(["workspace", "export"]) == 0
    assert calls == [False, False]
    (patch,) = (tmp_path / "exports").glob("*.patch")
    assert patch.read_bytes() == CRLF_DIFF


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
    for agent in config["agent"].values():
        if agent.get("disable"):
            continue
        prompt = agent["prompt"].removeprefix("{file:").removesuffix("}")
        assert (repo_root / "config" / prompt).is_file()


def test_opencode_host_tools_and_builtin_agents_are_off(repo_root):
    """Every action goes through the guard: OpenCode's own tools that touch the host, the web, or
    other agents are denied, its built-in agents are disabled, and only local models are listed."""
    config = json.loads((repo_root / "config" / "opencode.json").read_text(encoding="utf-8"))
    host_tools = {"edit", "bash", "webfetch", "websearch", "codesearch", "read", "glob", "grep"}
    host_tools |= {"list", "lsp", "skill", "external_directory", "task"}
    assert {tool: config["permission"].get(tool) for tool in host_tools} == dict.fromkeys(host_tools, "deny")
    assert set(config["permission"].values()) == {"deny"}
    for builtin in ("build", "plan", "general", "explore"):
        assert config["agent"][builtin] == {"disable": True}
    assert config["enabled_providers"] == ["aegis"]
    assert config["default_agent"] == "planner"
    # Only the planner may delegate, and only to the verifier.
    delegating = {name for name, agent in config["agent"].items() if "task" in agent.get("permission", {})}
    assert delegating == {"planner"}
    assert config["agent"]["planner"]["permission"]["task"] == {"*": "deny", "verifier": "allow"}


def test_planner_cannot_change_the_workspace(repo_root, code_worker_tools):
    """The planner keeps only the code worker's readers and run_check; every other worker tool is off.

    The tools come from the worker's MCP registry, so a new writer is caught whatever its policy rule says."""
    config = json.loads((repo_root / "config" / "opencode.json").read_text(encoding="utf-8"))
    allowed = {"list_files", "read_file", "diff", "run_check"}
    assert allowed.issubset(code_worker_tools)
    planner = config["agent"]["planner"]["tools"]
    left_on = {tool for tool in code_worker_tools - allowed if planner.get(f"aegis_code__{tool}") is not False}
    assert left_on == set()


def test_versions_agree(repo_root):
    import tomllib

    from aegis_guard import __version__ as guard_version
    from aegis_router import __version__ as router_version

    with (repo_root / "pyproject.toml").open("rb") as fh:
        project_version = tomllib.load(fh)["project"]["version"]
    assert aegis.VERSION == project_version == guard_version == router_version
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{aegis.VERSION}]" in changelog


def test_pull_uses_the_release_images():
    assert aegis.image_settings(pull=True) == {"AEGIS_REGISTRY": aegis.REGISTRY, "AEGIS_VERSION": aegis.VERSION}
    assert aegis.image_settings(pull=False) == {}


def test_compose_images_follow_registry_and_version(repo_root):
    import yaml

    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))
    # workspace-prepare runs the code worker's image (its prepare.py), so the installed environment
    # matches the worker's Python exactly; it has no image or build of its own.
    shares = {"workspace-prepare": "code-worker"}
    for name, service in compose["services"].items():
        image = shares.get(name, name)
        assert service["image"] == f"${{AEGIS_REGISTRY:-aegis-local}}/{image}:${{AEGIS_VERSION:-dev}}"
        assert ("build" in service) == (name not in shares)


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
