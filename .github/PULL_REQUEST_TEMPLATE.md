## What and why

<!-- What does this change, and what problem does it solve? Link the issue. -->

## Area

- [ ] Launcher (`aegis.py`)
- [ ] Model router
- [ ] Guard proxy (control plane: needs tests for every new decision)
- [ ] Sandbox worker
- [ ] OpenCode config, agents, or plugin
- [ ] Docs only

## How I tested it

<!-- Commands run, OS, container runtime, profile. -->

## Checklist

- [ ] `ruff check .` and `pytest` pass locally
- [ ] Docs updated (and `docs/PLAN.md` if a design decision changed)
- [ ] No new port binds to anything but loopback
- [ ] No secrets, tokens, model weights, or personal data in the diff
- [ ] Every commit is signed off (`git commit -s`)
