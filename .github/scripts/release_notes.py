"""Release helper for .github/workflows/release.yml. Standard library only.

    release_notes.py --check REF_TYPE REF_NAME   print version=... and publish=... for $GITHUB_OUTPUT
    release_notes.py VERSION                      print release notes for VERSION from CHANGELOG.md

--check fails when a tag doesn't match the version in aegis.py, so a release can't publish
images labeled with the wrong version.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = "madkatomega-sys/aegis-local"
SERVICES = ("model-router", "mcp-guard-proxy", "code-worker", "math-physics-mcp", "writing-auditor")


def code_version() -> str:
    match = re.search(r'^VERSION = "([^"]+)"', (ROOT / "aegis.py").read_text(encoding="utf-8"), re.M)
    if not match:
        sys.exit("VERSION not found in aegis.py")
    return match.group(1)


def check(ref_type: str, ref_name: str) -> None:
    version = code_version()
    if ref_type == "tag":
        tag_version = ref_name.removeprefix("v")
        if tag_version.split("-", 1)[0] != version:
            sys.exit(f"tag {ref_name} doesn't match aegis.py VERSION {version}")
        print(f"version={tag_version}")
        print("publish=true")
    else:
        print(f"version={version}-dryrun")
        print("publish=false")


def changelog_section(version: str) -> str:
    base = version.split("-", 1)[0]
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## \[{re.escape(base)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    if not match:
        sys.exit(f"CHANGELOG.md has no section for {base}")
    return match.group(1).strip()


def notes(version: str) -> str:
    images = "\n".join(f"ghcr.io/{REPO}/{name}:{version}" for name in SERVICES)
    return f"""{changelog_section(version)}

## Container images

Built for `linux/amd64` and `linux/arm64`, with an SBOM and build provenance, and signed with
Sigstore by this repository's release workflow.

```text
{images}
```

Run them with `python aegis.py up --pull`. To verify a signature:

```bash
cosign verify ghcr.io/{REPO}/model-router:{version} \\
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \\
  --certificate-identity-regexp '^https://github.com/{REPO}/\\.github/workflows/release\\.yml@refs/tags/v'
```

Aegis-Local ships no model weights. See the README for setup and docs/PLATFORMS.md for your OS.
"""


def main(argv: list[str]) -> None:
    if len(argv) == 4 and argv[1] == "--check":
        check(argv[2], argv[3])
    elif len(argv) == 2:
        print(notes(argv[1]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
