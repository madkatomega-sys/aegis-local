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


def test_notes_leave_out_the_changelog_link_definitions(release):
    notes = release.notes(aegis.VERSION)
    assert "[Unreleased]:" not in notes
    assert f"[{aegis.VERSION}]:" not in notes


def test_relative_links_point_at_the_tagged_files(release):
    section = (
        "See [the report](docs/validation/report.md#result), [the guide](./docs/PLATFORMS.md),\n"
        "[the spec](https://semver.org/), [mail](mailto:a@example.com), and [below](#fixed).\n"
        "\n"
        "[0.1.0]: https://github.com/madkatomega-sys/aegis-local/releases/tag/v0.1.0\n"
    )
    page = release.for_release_page(section, "0.2.0-rc.1")
    blob = "https://github.com/madkatomega-sys/aegis-local/blob/v0.2.0-rc.1"
    assert f"[the report]({blob}/docs/validation/report.md#result)" in page
    assert f"[the guide]({blob}/docs/PLATFORMS.md)" in page
    assert "[the spec](https://semver.org/)" in page
    assert "[mail](mailto:a@example.com)" in page
    assert "[below](#fixed)" in page
    assert "[0.1.0]:" not in page
    assert page.endswith("[below](#fixed).")


def test_every_relative_changelog_link_exists(release, repo_root):
    blob = f"https://github.com/madkatomega-sys/aegis-local/blob/v{aegis.VERSION}/"
    notes = release.notes(aegis.VERSION)
    paths = [m.group(1).split("#")[0] for m in release.LINK_TARGET.finditer(notes)]
    for path in [p.removeprefix(blob) for p in paths if p.startswith(blob)]:
        assert (repo_root / path).exists(), path
