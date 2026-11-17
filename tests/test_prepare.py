"""`aegis workspace prepare`: what it accepts, where it runs, and how the code worker uses the result."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def prepare():
    path = ROOT / "services" / "sandboxes" / "code-worker" / "prepare.py"
    spec = importlib.util.spec_from_file_location("code_worker_prepare", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


# -- which requirements are accepted ---------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "requests",
        "requests>=2.31,<3",
        "pint[numpy]>=0.24",
        "typing-extensions; python_version < '3.12'",
        "attrs==23.2.0 --hash=sha256:" + "a" * 64,
        "Django ~= 5.0",
    ],
)
def test_index_requirements_are_accepted(prepare, line):
    assert prepare.check_requirement(line) == line


@pytest.mark.parametrize(
    "line, reason",
    [
        ("--index-url https://evil.example/simple", "pip options"),
        ("--extra-index-url https://evil.example/simple", "pip options"),
        ("-e .", "pip options"),
        ("--find-links /tmp/wheels", "pip options"),
        ("--trusted-host evil.example", "pip options"),
        ("requests --index-url https://evil.example/simple", "pip options"),
        ("pkg @ https://evil.example/pkg.whl", "direct URLs"),
        ("git+https://github.com/x/y.git", "direct URLs"),
        ("./vendor/pkg", "not a package name"),
        ("vendor/pkg", "local paths"),
        ("pkg-1.0-py3-none-any.whl", "local paths"),
    ],
)
def test_options_urls_and_paths_are_refused(prepare, line, reason):
    """Each of these could send pip to another index or to files the install container can't see."""
    with pytest.raises(prepare.PrepareError, match=reason):
        prepare.check_requirement(line)


def test_collect_reads_requirement_files_and_includes(prepare, repo):
    (repo / "requirements.txt").write_text("# runtime\nrequests>=2  # http\n-r requirements/extra.txt\n")
    (repo / "requirements").mkdir()
    (repo / "requirements" / "extra.txt").write_text("pint\\\n>=0.24\n\n")
    (repo / "requirements-dev.txt").write_text("pytest-mock\n")
    assert prepare.collect(repo) == {
        "requirements.txt": ["requests>=2", "pint >=0.24"],
        "requirements-dev.txt": ["pytest-mock"],
    }


def test_collect_reads_pyproject_dependencies_extras_and_groups(prepare, repo):
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'x'\ndependencies = ['attrs']\n"
        "[project.optional-dependencies]\ntest = ['hypothesis']\ndocs = ['sphinx']\n"
        "[dependency-groups]\nlint = ['ruff']\ndev = ['pytest-cov', {include-group = 'lint'}]\n"
    )
    assert prepare.collect(repo) == {"pyproject.toml": ["attrs", "hypothesis", "pytest-cov", "ruff"]}


def test_collect_with_named_files_reads_only_those(prepare, repo):
    (repo / "requirements.txt").write_text("requests\n")
    (repo / "ci.txt").write_text("pint\n")
    assert prepare.collect(repo, ["ci.txt"]) == {"ci.txt": ["pint"]}


@pytest.mark.parametrize("name, message", [("../outside.txt", "outside the repository"), ("missing.txt", "not found")])
def test_collect_stays_inside_the_repository(prepare, repo, name, message):
    (repo.parent / "outside.txt").write_text("requests\n")
    with pytest.raises(prepare.PrepareError, match=message):
        prepare.collect(repo, [name])


def test_collect_refuses_a_bad_line_anywhere(prepare, repo):
    (repo / "requirements.txt").write_text("requests\n--index-url https://evil.example/simple\n")
    with pytest.raises(prepare.PrepareError, match="pip options"):
        prepare.collect(repo)


def test_collect_needs_a_loaded_workspace(prepare, tmp_path):
    with pytest.raises(prepare.PrepareError, match="workspace is empty"):
        prepare.collect(tmp_path)


def test_install_checks_its_input_again(prepare, tmp_path):
    """The install container takes its list from stdin, so it checks every line itself."""
    with pytest.raises(prepare.PrepareError, match="pip options"):
        prepare.install(["--index-url https://evil.example/simple"], False, tmp_path / "venv")
    assert not (tmp_path / "venv").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="install runs in the Linux worker image (venv/bin, lib/python*)")
def test_install_without_requirements_builds_an_empty_environment(prepare, tmp_path):
    venv = tmp_path / "venv"
    assert prepare.install([], False, venv) == "nothing to install"
    marker = json.loads((venv / prepare.MARKER_FILE).read_text())
    assert marker["requirements"] == []
    assert marker["allow_source_builds"] is False
    (pth,) = venv.glob("**/aegis-workspace.pth")
    assert pth.read_text().splitlines() == [str(prepare.REPO), str(prepare.REPO / "src")]


# -- where it runs ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def compose():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_prepare_service_sees_no_repository_and_holds_no_tokens(compose):
    service = compose["services"]["workspace-prepare"]
    mounts = {volume.split(":")[0]: volume for volume in service["volumes"]}
    assert set(mounts) == {"aegis-venv", "pip-cache"}
    assert not any("TOKEN" in key or key == "GITHUB_PAT" for key in service.get("environment", {}))


def test_prepare_service_has_its_own_network_and_is_hardened(compose):
    service = compose["services"]["workspace-prepare"]
    assert service["networks"] == ["prepare-egress"]
    assert all("prepare-egress" not in other.get("networks", []) for name, other in compose["services"].items()
               if name != "workspace-prepare")  # fmt: skip
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["profiles"] == ["prepare"]  # never started by `up`
    assert "ports" not in service


def test_code_worker_mounts_the_environment_read_only(compose):
    assert "aegis-venv:/venv:ro" in compose["services"]["code-worker"]["volumes"]
    assert compose["networks"]["sandbox-code"]["internal"] is True


# -- how the worker uses it ------------------------------------------------------------------


def test_worker_checks_run_with_the_prepared_environment(code_worker, tmp_path, monkeypatch):
    venv = tmp_path / "venv"
    monkeypatch.setattr(code_worker, "VENV", venv)
    assert "VIRTUAL_ENV" not in code_worker._git_env()
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("")
    env = code_worker._git_env()
    assert env["VIRTUAL_ENV"] == str(venv)
    assert env["PATH"].startswith(str(venv / "bin"))


# -- the launcher ----------------------------------------------------------------------------


def test_launcher_prepare_collects_in_the_worker_and_installs_in_the_prepare_service(monkeypatch, capsys):
    import aegis

    calls = []
    found = {"requirements.txt": ["pint>=0.24"]}

    def fake_compose(runtime, *args, env, capture=False, text=True, stdin=None):
        calls.append((args, stdin))
        out = json.dumps(found) if "collect" in args else ""
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr(aegis, "compose", fake_compose)
    assert aegis.workspace_prepare("docker", {}, ["requirements.txt"], False) == 0
    (collect, _), (install, stdin) = calls
    assert "code-worker" in collect and "--requirements=requirements.txt" in collect
    assert "workspace-prepare" in install and "--allow-source-builds" not in install
    assert json.loads(stdin) == found
    assert "pint>=0.24" in capsys.readouterr().out
