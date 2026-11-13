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

Before your first pull request can be merged, you need to be on the approved contributor list.
Open a **Contributor access request** issue; see [GOVERNANCE.md](GOVERNANCE.md#becoming-a-contributor).

1. Fork, then create **one branch per pull request** from the latest `main`
   (`feature/<topic>` or `fix/<topic>`). Nobody, including maintainers, pushes to `main` directly.
2. Keep each PR to one concern. Split refactors from behavior changes.
3. Run `ruff check .` and `pytest` locally.
4. Update docs when behavior or configuration changes. If you change a design decision, update
   [docs/PLAN.md](docs/PLAN.md) in the same PR.
5. Sign off every commit (see below).
6. Fill in the pull request template, including how you tested and on which OS.
7. **Keep your branch up to date with `main`.** A pull request can't merge until it contains
   the latest `main` and CI has passed on the combined code. Use **Update branch** on the pull
   request, or locally:

   ```bash
   git fetch upstream
   git rebase upstream/main      # or: git merge upstream/main
   git push --force-with-lease   # only needed after a rebase
   ```

To merge, a pull request needs an approving review from the owner or a pr-approver (the owner
alone for governance and licensing files), passing CI on Linux and Windows, a green
`contributor-approved` check, and every review conversation resolved. Changes to the guard
proxy's policy logic should get two reviews when two approvers are available. The full rules
are in [GOVERNANCE.md](GOVERNANCE.md).

## Developer Certificate of Origin

Aegis-Local uses the [Developer Certificate of Origin 1.1](https://developercertificate.org/)
instead of a contributor license agreement. By adding a `Signed-off-by` line to a commit, you
certify that you wrote the change or otherwise have the right to submit it under the project's
license (Apache-2.0).

```bash
git commit -s -m "guard: detect three-step oscillation"
```

This adds a line like `Signed-off-by: Your Name <you@example.com>`. Use your real name or a
consistent public identity. Reviewers won't approve pull requests with unsigned commits; fix
them with `git commit --amend -s` or `git rebase --signoff main`.

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

Roles (owner, pr-approver, contrib-approver, approved contributor), what each can approve, and
how the repository is protected are described in [GOVERNANCE.md](GOVERNANCE.md). Approvers are
invited from contributors with a sustained record of good reviews and merged work.
