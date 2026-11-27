"""`aegis.py models`: the pinned catalog, hardware detection, profile suggestions, and checked downloads."""

import hashlib
import re
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import aegis

# -- the catalog -----------------------------------------------------------------------------


def test_every_catalog_entry_is_pinned():
    catalog = aegis.load_catalog()
    assert catalog
    for model_id, entry in catalog.items():
        assert re.fullmatch(r"[a-z0-9._-]+", model_id), model_id
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), model_id
        assert isinstance(entry["size"], int) and entry["size"] > 100_000_000, model_id
        assert entry["path"].endswith(".gguf") and "/" not in entry["path"], model_id
        assert re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", entry["repo"]), model_id
        assert entry["license"], model_id


def test_measured_entries_point_at_evidence(repo_root):
    for model_id, entry in aegis.load_catalog().items():
        if "measured" in entry:
            assert (repo_root / entry["measured"]).is_file(), model_id


@pytest.mark.parametrize("name", aegis.list_profiles())
def test_every_profile_can_be_downloaded(name):
    catalog = aegis.load_catalog()
    profile = aegis.load_profile(name)
    for tier in aegis.TIERS:
        assert profile["tiers"][tier]["download"] in catalog, (name, tier)


@pytest.mark.parametrize("name", ["nvidia-12gb", "nvidia-8gb", "cpu-only"])
def test_measured_profiles_download_what_was_measured(name):
    profile = aegis.load_profile(name)
    plan = aegis.model_plan(profile, aegis.load_catalog(), {})
    catalog = aegis.load_catalog()
    assert all("measured" in catalog[model_id] for model_id in plan.values())
    assert profile["measured"]["tier1_tokens_per_second"] > 0


# -- detection and suggestions ---------------------------------------------------------------


def test_parse_nvidia_smi():
    out = "NVIDIA GeForce RTX 4070, 12282\nNVIDIA RTX A6000, 49140\nnot a gpu line\n"
    assert aegis.parse_nvidia_smi(out) == [
        {"vendor": "nvidia", "name": "NVIDIA GeForce RTX 4070", "memory_gb": 12.0},
        {"vendor": "nvidia", "name": "NVIDIA RTX A6000", "memory_gb": 48.0},
    ]


def test_parse_adapters_keeps_supported_gpus_only():
    out = "\n".join(
        [
            "AMD Radeon RX 7900 XTX|25753026560",
            "Intel(R) Arc(TM) B580 Graphics|12884901888",
            "Intel(R) UHD Graphics 630|",  # integrated: not a profile target
            "Microsoft Basic Display Adapter|0",
            "AMD Radeon 8060S Graphics|",  # memory not reported
        ]
    )
    assert aegis.parse_adapters(out) == [
        {"vendor": "amd", "name": "AMD Radeon RX 7900 XTX", "memory_gb": 24.0},
        {"vendor": "intel", "name": "Intel(R) Arc(TM) B580 Graphics", "memory_gb": 12.0},
        {"vendor": "amd", "name": "AMD Radeon 8060S Graphics", "memory_gb": None},
    ]


def gpu(vendor, memory, name="card"):
    return {"vendor": vendor, "name": name, "memory_gb": memory}


@pytest.mark.parametrize(
    "gpus, expected",
    [
        ([gpu("nvidia", 12.0)], "nvidia-12gb"),
        ([gpu("nvidia", 8.0)], "nvidia-8gb"),
        ([gpu("nvidia", 16.0)], "nvidia-16gb"),
        ([gpu("nvidia", 24.0)], "nvidia-24gb-plus"),
        ([gpu("nvidia", 4.0)], "cpu-only"),
        ([gpu("amd", 16.0)], "amd-16gb"),
        ([gpu("amd", 24.0)], "amd-24gb"),
        ([gpu("amd", 96.0)], "amd-unified-128gb"),
        ([gpu("intel", 12.0)], "intel-arc-12gb"),
        ([gpu("intel", 16.0)], "intel-arc-16gb"),
        ([gpu("amd", 8.0), gpu("nvidia", 12.0)], "nvidia-12gb"),  # the strongest card wins
        ([gpu("amd", None)], "cpu-only"),  # unknown memory: says so, falls back
        ([], "cpu-only"),
    ],
)
def test_suggest_profile(gpus, expected):
    profile, reason = aegis.suggest_profile(gpus)
    assert profile == expected
    assert reason
    assert profile in aegis.list_profiles()


# -- downloads -------------------------------------------------------------------------------


class FakeHub(BaseHTTPRequestHandler):
    """Serves /<repo>/resolve/main/<file> with Range support, like Hugging Face's file endpoint."""

    files: dict[str, bytes] = {}
    requests: list[dict[str, str]] = []
    honour_range = True

    def do_GET(self):  # noqa: N802
        FakeHub.requests.append({"path": self.path, "range": self.headers.get("Range", "")})
        data = FakeHub.files.get(self.path)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        start = 0
        match = re.fullmatch(r"bytes=(\d+)-", self.headers.get("Range", ""))
        if match and FakeHub.honour_range:
            start = int(match.group(1))
            self.send_response(206)
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        self.wfile.write(data[start:])

    def log_message(self, *args):
        pass


@pytest.fixture
def hub():
    FakeHub.files, FakeHub.requests, FakeHub.honour_range = {}, [], True
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def entry_for(data: bytes, path="model.gguf") -> dict:
    return {"repo": "org/model-GGUF", "path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_download_checks_the_hash_and_keeps_the_file(hub, tmp_path):
    data = b"gguf" * 100_000
    entry = entry_for(data)
    FakeHub.files["/org/model-GGUF/resolve/main/model.gguf"] = data
    aegis.download_model(entry, tmp_path / "model.gguf", base_url=hub)
    assert (tmp_path / "model.gguf").read_bytes() == data
    assert not (tmp_path / "model.gguf.part").exists()


def test_download_refuses_a_file_that_does_not_match(hub, tmp_path):
    data = b"gguf" * 1000
    entry = {**entry_for(data), "sha256": "0" * 64}
    FakeHub.files["/org/model-GGUF/resolve/main/model.gguf"] = data
    with pytest.raises(SystemExit):
        aegis.download_model(entry, tmp_path / "model.gguf", base_url=hub)
    assert list(tmp_path.iterdir()) == []  # nothing kept, not even the partial file


def test_download_resumes_a_partial_file(hub, tmp_path):
    data = bytes(range(256)) * 4000
    entry = entry_for(data)
    FakeHub.files["/org/model-GGUF/resolve/main/model.gguf"] = data
    (tmp_path / "model.gguf.part").write_bytes(data[:300_000])
    aegis.download_model(entry, tmp_path / "model.gguf", base_url=hub)
    assert (tmp_path / "model.gguf").read_bytes() == data
    assert FakeHub.requests[-1]["range"] == "bytes=300000-"


def test_download_starts_over_if_the_server_ignores_the_range(hub, tmp_path):
    data = bytes(range(256)) * 4000
    entry = entry_for(data)
    FakeHub.files["/org/model-GGUF/resolve/main/model.gguf"] = data
    FakeHub.honour_range = False
    (tmp_path / "model.gguf.part").write_bytes(b"stale bytes that must not be kept")
    aegis.download_model(entry, tmp_path / "model.gguf", base_url=hub)
    assert (tmp_path / "model.gguf").read_bytes() == data


# -- .env ------------------------------------------------------------------------------------


def test_set_env_values_keeps_everything_else(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# models\nAEGIS_PROFILE=cpu-only\n#AEGIS_TIER0_MODEL=commented\nAEGIS_TIER0_MODEL=\nOTHER=1\n")
    aegis.set_env_values(env, {"AEGIS_TIER0_MODEL": "C:/m/a.gguf", "AEGIS_PROFILE": "nvidia-12gb", "NEW": "x"})
    assert env.read_text().splitlines() == [
        "# models",
        "AEGIS_PROFILE=nvidia-12gb",
        "#AEGIS_TIER0_MODEL=commented",
        "AEGIS_TIER0_MODEL=C:/m/a.gguf",
        "OTHER=1",
        "NEW=x",
    ]


def test_set_env_values_keeps_windows_line_endings(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"# models\r\nAEGIS_PROFILE=cpu-only\r\nOTHER=1\r\n")
    aegis.set_env_values(env, {"AEGIS_PROFILE": "nvidia-12gb"})
    assert env.read_bytes() == b"# models\r\nAEGIS_PROFILE=nvidia-12gb\r\nOTHER=1\r\n"


def test_set_env_values_leaves_an_unchanged_file_byte_for_byte(tmp_path):
    env = tmp_path / ".env"
    original = b"# a\r\nAEGIS_PROFILE=nvidia-12gb\r\n\r\nX=\r\n"
    env.write_bytes(original)
    aegis.set_env_values(env, {"AEGIS_PROFILE": "nvidia-12gb"})
    assert env.read_bytes() == original


# -- the command -----------------------------------------------------------------------------


@pytest.fixture
def small_catalog(monkeypatch, tmp_path, hub):
    """A two-file catalog served by the fake hub, a profile that names it, and .env in tmp_path."""
    files = {"tier0": b"tier zero " * 5000, "tier1": b"tier one " * 7000}
    catalog = {}
    for tier, data in files.items():
        catalog[f"{tier}-model"] = {**entry_for(data, f"{tier}.gguf"), "name": f"{tier} model", "license": "mit"}
        FakeHub.files[f"/org/model-GGUF/resolve/main/{tier}.gguf"] = data
    profile = aegis.load_profile("cpu-only")
    for tier in aegis.TIERS:
        profile["tiers"][tier]["download"] = f"{tier}-model"
    monkeypatch.setattr(aegis, "load_catalog", lambda: catalog)
    monkeypatch.setattr(aegis, "load_profile", lambda name: profile)
    monkeypatch.setattr(aegis, "detect_gpus", lambda: [])
    monkeypatch.setattr(aegis, "system_ram_gb", lambda: 32.0)
    monkeypatch.setattr(aegis, "ROOT", tmp_path)
    monkeypatch.setattr(aegis, "TOKENS", tmp_path / "tokens.env")
    monkeypatch.setattr(aegis, "download_model", partial(aegis.download_model, base_url=hub))
    (tmp_path / ".env").write_text("AEGIS_PROFILE=cpu-only\nAEGIS_TIER0_MODEL=\nAEGIS_TIER1_MODEL=\n")
    return files


def test_get_downloads_checks_and_sets_env(small_catalog, tmp_path, monkeypatch):
    for key in ("AEGIS_PROFILE", "AEGIS_TIER0_MODEL", "AEGIS_TIER1_MODEL"):
        monkeypatch.delenv(key, raising=False)
    models = tmp_path / "models"
    assert aegis.main(["models", "get", "--dir", str(models), "--yes"]) == 0
    assert (models / "tier0.gguf").read_bytes() == small_catalog["tier0"]
    env = aegis.read_env_file(tmp_path / ".env")
    assert env["AEGIS_TIER0_MODEL"] == (models / "tier0.gguf").as_posix()
    assert env["AEGIS_TIER1_MODEL"] == (models / "tier1.gguf").as_posix()
    # A second run finds both files, checks their hashes, and downloads nothing.
    before = len(FakeHub.requests)
    assert aegis.main(["models", "get", "--dir", str(models), "--yes"]) == 0
    assert len(FakeHub.requests) == before


def test_get_fetches_a_file_shared_by_both_tiers_once(small_catalog, tmp_path, monkeypatch):
    for key in ("AEGIS_PROFILE", "AEGIS_TIER0_MODEL", "AEGIS_TIER1_MODEL"):
        monkeypatch.delenv(key, raising=False)
    models = tmp_path / "models"
    args = ["models", "get", "--dir", str(models), "--tier1", "tier0-model", "--yes"]
    assert aegis.main(args) == 0
    assert [r["path"] for r in FakeHub.requests] == ["/org/model-GGUF/resolve/main/tier0.gguf"]
    env = aegis.read_env_file(tmp_path / ".env")
    assert env["AEGIS_TIER0_MODEL"] == env["AEGIS_TIER1_MODEL"] == (models / "tier0.gguf").as_posix()


def test_get_uses_the_suggested_profile_when_env_names_none(small_catalog, tmp_path, monkeypatch):
    monkeypatch.delenv("AEGIS_PROFILE", raising=False)
    (tmp_path / ".env").write_text("AEGIS_PROFILE=\n")
    monkeypatch.setattr(aegis, "detect_gpus", lambda: [gpu("nvidia", 8.0)])
    assert aegis.main(["models", "get", "--dir", str(tmp_path / "models"), "--yes"]) == 0
    assert aegis.read_env_file(tmp_path / ".env")["AEGIS_PROFILE"] == "nvidia-8gb"


def test_get_asks_before_downloading(small_catalog, tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert aegis.main(["models", "get", "--dir", str(tmp_path / "models")]) == 1
    assert FakeHub.requests == []


def test_get_only_fetches_and_sets_one_tier(small_catalog, tmp_path, monkeypatch):
    for key in ("AEGIS_PROFILE", "AEGIS_TIER0_MODEL", "AEGIS_TIER1_MODEL"):
        monkeypatch.delenv(key, raising=False)
    models = tmp_path / "models"
    assert aegis.main(["models", "get", "--dir", str(models), "--only", "tier1", "--yes"]) == 0
    assert [r["path"] for r in FakeHub.requests] == ["/org/model-GGUF/resolve/main/tier1.gguf"]
    assert sorted(p.name for p in models.iterdir()) == ["tier1.gguf"]
    env = aegis.read_env_file(tmp_path / ".env")
    assert env["AEGIS_TIER1_MODEL"] == (models / "tier1.gguf").as_posix()
    assert env["AEGIS_TIER0_MODEL"] == ""  # left as it was


def test_max_gb_refuses_a_bigger_download_before_fetching(small_catalog, tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("asked although the plan was too big"))
    total = sum(len(data) for data in small_catalog.values())  # both files, as bytes
    limit = (total - 1) / 1e9
    with pytest.raises(SystemExit) as refused:
        aegis.main(["models", "get", "--dir", str(tmp_path / "models"), "--max-gb", f"{limit:.9f}"])
    assert refused.value.code != 0
    assert FakeHub.requests == []
    assert aegis.read_env_file(tmp_path / ".env")["AEGIS_TIER0_MODEL"] == ""


def test_max_gb_counts_only_what_is_missing(small_catalog, tmp_path, monkeypatch):
    for key in ("AEGIS_PROFILE", "AEGIS_TIER0_MODEL", "AEGIS_TIER1_MODEL"):
        monkeypatch.delenv(key, raising=False)
    models = tmp_path / "models"
    models.mkdir()
    (models / "tier1.gguf").write_bytes(small_catalog["tier1"])  # already here: checked, not fetched
    limit = len(small_catalog["tier0"]) / 1e9
    assert aegis.main(["models", "get", "--dir", str(models), "--max-gb", f"{limit:.9f}", "--yes"]) == 0
    assert [r["path"] for r in FakeHub.requests] == ["/org/model-GGUF/resolve/main/tier0.gguf"]


def test_get_refuses_an_unknown_model(small_catalog, tmp_path):
    with pytest.raises(SystemExit):
        aegis.main(["models", "get", "--dir", str(tmp_path / "m"), "--tier1", "not-in-the-catalog", "--yes"])
