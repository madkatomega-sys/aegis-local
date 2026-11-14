"""The release helper refuses mismatched tags and finds the changelog section."""

import importlib.util

import pytest

import aegis


@pytest.fixture
def release(repo_root):
    spec = importlib.util.spec_from_file_location(
        "release_notes", repo_root / ".github" / "scripts" / "release_notes.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matching_tag_publishes(release, capsys):
    release.check("tag", f"v{aegis.VERSION}")
    assert capsys.readouterr().out.split() == [f"version={aegis.VERSION}", "publish=true"]


def test_release_candidate_tag_publishes(release, capsys):
    release.check("tag", f"v{aegis.VERSION}-rc.1")
    assert f"version={aegis.VERSION}-rc.1" in capsys.readouterr().out


def test_mismatched_tag_is_refused(release):
    with pytest.raises(SystemExit):
        release.check("tag", "v9.9.9")


def test_branch_run_is_a_dry_run(release, capsys):
    release.check("branch", "main")
    assert "publish=false" in capsys.readouterr().out


def test_notes_include_changelog_and_images(release):
    notes = release.notes(aegis.VERSION)
    assert "### Added" in notes
    assert f"ghcr.io/madkatomega-sys/aegis-local/mcp-guard-proxy:{aegis.VERSION}" in notes
    assert "cosign verify" in notes
