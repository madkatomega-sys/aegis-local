# Contributing to Aegis-Local

Thanks for helping. This project aims to make local AI trustworthy through plain, reviewable
engineering, so the bar for contributions is clarity: small changes, clear reasons, tests for
anything the control plane decides.

## Ways to contribute

- **Report a bug** or a confusing doc with the issue templates.
- **Submit a GPU profile** with measured numbers from your hardware.
- **Build or harden a sandbox worker.**
- **Review pull requests.** A careful review is as valuable as code.
- **Propose a design change** by opening an issue labeled `design` before writing a large patch.

## Ground rules for the control plane

The router, guard proxy, and `aegis.py` are the trusted core. Changes there follow extra rules:

1. **No model in the decision path.** Allow, deny, hold, and trip decisions are made by
   deterministic code. A model may *produce input* to a decision (the extractor's JSON), never the
   decision itself.
2. **Fail closed.** If a policy can't be evaluated (missing config, unknown tool, malformed
   arguments), the answer is deny or hold, not allow.
3. **Every decision is tested.** New policy branches come with unit tests in `tests/`.
4. **Nothing binds to `0.0.0.0`.** Published ports bind to loopback.
5. **`aegis.py` stays standard-library only**, so first run needs no `pip install`.
6. **Enforcement never moves into OpenCode plugins or agent prompts.** Those are conveniences.

## Development setup

```bash
git clone https://github.com/madkatomega-sys/aegis-local.git
cd aegis-local
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
pytest
docker compose config --quiet
```

## Pull requests

1. Fork, then branch from `main` (`feature/<topic>` or `fix/<topic>`).
2. Keep each PR to one concern. Split refactors from behavior changes.
3. Run `ruff check .` and `pytest` locally.
4. Update docs when behavior or configuration changes. If you change a design decision, update
   [docs/PLAN.md](docs/PLAN.md) in the same PR.
5. Sign off every commit (see below).
6. Fill in the pull request template, including how you tested and on which OS.

A maintainer reviews every PR. Changes to the control plane need one approving review from a
maintainer; changes to the guard proxy's policy logic should get two when two maintainers are
available.

## Developer Certificate of Origin

Aegis-Local uses the [Developer Certificate of Origin 1.1](https://developercertificate.org/)
instead of a contributor license agreement. By adding a `Signed-off-by` line to a commit, you
certify that you wrote the change or otherwise have the right to submit it under the project's
license (Apache-2.0).

```bash
git commit -s -m "guard: detect three-step oscillation"
```

This adds a line like `Signed-off-by: Your Name <you@example.com>`. Use your real name or a
consistent public identity. Pull requests with unsigned commits can't be merged; fix them with
`git commit --amend -s` or `git rebase --signoff main`.

## Licensing of contributions

All contributions are licensed under the Apache License 2.0, as stated in Section 5 of the
license. Don't submit code copied from projects under incompatible licenses (for example GPL or
AGPL, or code with no license). If you include code from a permissively licensed project, keep its
copyright notice and mention it in the PR.

Never commit model weights, API keys, tokens, or personal data.

## Style

- Python: formatted and linted with `ruff`; type hints on public functions; Python 3.11+.
- TypeScript (plugin): keep it small and dependency-free beyond `@opencode-ai/plugin`.
- Config: YAML for services, TOML for host profiles, two-space indentation.
- Docs: plain language. Explain *why* as well as *what*.
- Commit messages: imperative mood, prefixed with the area (`router:`, `guard:`, `docs:`,
  `sandbox/math:`, `launcher:`, `ci:`).

## Project governance

The project is maintained by its founder and, as the community grows, by contributors invited as
maintainers after a sustained record of good reviews and merged work. Decisions are made in issues
and pull requests, in the open. When maintainers disagree, the founder decides, and the reasoning
is written down in the relevant issue or in [docs/PLAN.md](docs/PLAN.md).
