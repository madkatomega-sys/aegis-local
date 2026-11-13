# Governance

How decisions are made in Aegis-Local, who can approve what, and how the repository protects
itself. The rules below are enforced by GitHub rulesets, `CODEOWNERS`, and the contributor gate
workflow, not just by convention.

## Roles

| Role | Who | Can |
|---|---|---|
| **Owner** | [madkatomega-sys](https://github.com/madkatomega-sys) | Everything, including repository settings, rulesets, releases, and governance files |
| **pr-approver** | Listed on the `*` line of [`.github/CODEOWNERS`](.github/CODEOWNERS) | Approve pull requests that change code, docs, and config |
| **contrib-approver** | Listed on the `approved-contributors.txt` line of `CODEOWNERS` | Approve pull requests that add people to the approved contributor list |
| **Approved contributor** | Listed in [`.github/approved-contributors.txt`](.github/approved-contributors.txt) | Open pull requests that can be merged once reviewed |
| **Anyone** | Everyone else | Fork, open issues, open pull requests, and request contributor access |

pr-approvers and contrib-approvers are repository collaborators with the **Write** role, since
GitHub requires code owners to have write access. Nobody but the owner holds Maintain or Admin.

The repository belongs to a personal account, so these roles are lists of usernames rather than
GitHub teams. If the project moves to a GitHub organization, each role becomes a team and
`CODEOWNERS` lists the team instead.

## Becoming a contributor

1. Fork the repository and read [CONTRIBUTING.md](CONTRIBUTING.md).
2. Open a **Contributor access request** issue.
3. The owner or a contrib-approver adds your username to `approved-contributors.txt` in a pull
   request, which the owner or another contrib-approver approves.

You can open pull requests before you're approved. The `contributor-approved` check stays red
until you are, and the pull request can't be merged while it's red. The check reads the list
from `main`, so editing the list in your own pull request doesn't change the result.

CI on pull requests from forks waits for a collaborator to approve the workflow run, so
unreviewed code never runs on the project's runners automatically.

## How a change reaches `main`

Every change, including the owner's, arrives as a pull request from its own branch. Nobody
pushes to `main` directly. To merge, a pull request needs:

| Requirement | Enforced by |
|---|---|
| Opened from a separate branch; no direct pushes, force pushes, or deletion of `main` | `main` ruleset |
| Author is an approved contributor | `contributor-approved` check |
| CI passes: lint and tests on Linux and Windows, Compose validation | Required status checks |
| **Branch is up to date with `main`**, and CI passed on that combined code | Required status checks, strict mode |
| One approving review from a code owner: the owner or a pr-approver (contrib-approver for the contributor list; the owner alone for governance files) | `main` ruleset, `CODEOWNERS` |
| Approvals still apply to the latest code; new commits that change the diff dismiss them | `main` ruleset |
| Every review conversation resolved | `main` ruleset |

If `main` has moved since a pull request was opened, use **Update branch** on the pull request,
or merge or rebase `main` into the branch locally. CI then runs again on the combined code
before the pull request can merge. This stops a pull request from silently undoing work that
landed after it was opened.

The owner can't approve their own pull requests, and owner-only files have no other reviewer,
so the owner is allowed to bypass the merge requirements on a pull request. The bypass works
only through a pull request (the owner can't push to `main` directly either), and GitHub records
every bypass. By convention the owner still waits for green CI and an up-to-date branch, and
uses the bypass only for the missing second review or to fix broken CI.

## What can't be changed without the owner

- **Repository settings, rulesets, and deletion** are available only to the Admin role, which
  only the owner holds.
- **`main` history** can't be force-pushed or deleted by anyone.
- **Release tags** (`v*`) can be created, moved, or deleted only by the owner.
- **Governance and licensing files** (`.github/`, `LICENSE`, `NOTICE`, `GOVERNANCE.md`,
  `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`) need the owner's approval to change.

Forking is always allowed. A fork is the forker's own copy under the Apache-2.0 license; it
can't change this repository.

## Decisions

Design decisions are discussed in issues and pull requests, in the open. When maintainers
disagree, the owner decides, and the reasoning is recorded in the issue or in
[docs/PLAN.md](docs/PLAN.md).
