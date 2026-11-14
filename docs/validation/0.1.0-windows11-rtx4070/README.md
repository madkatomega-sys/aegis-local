# Validation of 0.1.0 on Windows 11 with an RTX 4070

This is the evidence that release 0.1.0 works end to end on real hardware: native `llama-server`
tiers on the GPU and CPU, the container stack on Docker Desktop, and every guard defense
exercised against a live model. Each claim below links to the raw transcript it comes from. The
transcripts are unedited except that the machine's LAN address is replaced with
`<LAN address>`.

**Result: pass.** Every check passed on both the CUDA and the Vulkan build of `llama-server`.
Getting there took three runs: the first two found six problems, all fixed in this release and
described [below](#what-validation-found-and-the-fixes).

| | |
|---|---|
| Code under test | commit `136a449` on the local validation branch (E1 to E6); same code as release `v0.1.0` (see below) |
| Date | 2026-11-13 |
| Machine | Windows 11 Pro build 26300, Intel Core i9-9900K (8 cores), 31.9 GB RAM |
| GPU | NVIDIA GeForce RTX 4070, 12 GB, driver 610.88 |
| Containers | Docker Desktop, Engine 29.9.3, Compose 5.7.0 (WSL2 backend) |
| Inference | llama.cpp b12361 (commit `951d465f5`), official CUDA 13.5 and Vulkan builds |
| Model | Qwen3-4B Q4_K_M (sha256 `7485fe6f…34fdf5`) for both tiers |
| Profile | `nvidia-12gb`: tier 0 on the CPU (port 8080), tier 1 on the GPU (port 8081) |

Full details: [E0-environment.txt](E0-environment.txt).

The commit hashes in these transcripts (`136a449`, and `7cbc90d` and `1d85577` for the earlier
runs) are from the local validation branch. That branch was squash-merged into `main` as
`253ace0`, which is tagged `v0.1.0`, so those hashes don't exist in this repository. The
release's code is the code validated here, with two differences:

- Dependabot raised FastAPI's minimum version from `>=0.162.3` to `>=0.162.4` in
  `services/mcp-guard-proxy/requirements.txt` and `services/model-router/requirements.txt`.
  The validated images ran FastAPI 0.162.7, which meets the new minimum.
- `docs/PLATFORMS.md` gained the "Verified configurations" section that links to this report.

## What was run

| Step | What it checks | Result | Evidence |
|---|---|---|---|
| E1 | Lint (`ruff check`, `ruff format --check`) and the unit and HTTP-level test suite, on the Windows host | 116 passed, lint clean | [E1](E1-lint-and-tests.txt) |
| E2 | All five container images build; Python and package versions inside each; each runs as a non-root user | 5 built, all Python 3.15.2, uid 10001 | [E2](E2-image-builds.txt) |
| E3 | The full stack with a stub model, using the same overlays and smoke test CI runs | 31 of 31 checks | [E3](E3-stack-smoke-stub-model.txt) |
| E4b | Tier 0's extraction setup as shipped, run on the GPU with the CUDA build: 100 requests at temperature 0.8 | 100 of 100 valid JSON | [E4b](E4b-schema-100-cuda.txt) |
| E4c | The same 100 requests with the Vulkan build | 100 of 100 valid JSON | [E4c](E4c-schema-100-vulkan.txt) |
| E5 | The complete walkthrough on the CUDA build (below) | 53 of 53 checks | [E5](E5-walkthrough-cuda.txt) |
| E6 | The same walkthrough on the Vulkan build, with 25 schema requests per tier instead of 100 | 53 of 53 checks | [E6](E6-walkthrough-vulkan.txt) |

The walkthrough (E5, E6) drives the release the way a user would, through `aegis.py` and the
guard's MCP endpoint:

1. `doctor`, then `up --no-build` starts both `llama-server` tiers and the stack, and `status`
   reports them healthy.
2. The smoke test against the live model: router aliases and routing; 100 extractions on
   tier 0 and 100 schema-constrained answers from tier 1, all parsed and checked against the
   schema; guard authentication and tool filtering; exact math and unit conversion; a runaway
   computation stopped at its time limit; the writing auditor's search and quote verification.
3. Prompt injection: a fixture GitHub server, marked untrusted, returns an issue that tells the
   agent to delete every test and push. The guard passes on only the extractor's structured
   summary (the model lists the instruction under `requested_actions`, as data), marks the
   session tainted, and holds the next mutating call for approval. An approval works once.
4. The coding loop: `workspace load` copies a small repository with a failing test into the
   sandbox; the agent lists files, runs pytest (fails), applies a fix, runs pytest (passes),
   runs ruff; `workspace export` writes a patch, and `git apply --check` confirms it applies to
   the original checkout.
5. Approvals and the breaker from the command line: `pending`, `approve`, `deny`; a repeated
   failing call trips the breaker, `status` shows the trip, `reset` clears it.
6. Network exposure: every harness port (4000, 4100, 8080, 8081) listens on `127.0.0.1` only, and
   none answers on the machine's LAN address. Sandbox isolation (no internet, no router, no
   other sandboxes, read-only root) is checked from inside each sandbox.
7. `down -v` and `aegis.py down` remove everything, and the ports are closed afterwards.

## Measurements

| | CUDA (E5) | Vulkan (E6) |
|---|---|---|
| Tier 0 generation, on the CPU (i9-9900K) | 12.2 tok/s median over 102 requests | 11.5 tok/s median over 27 requests |
| Tier 1 generation, on the RTX 4070 | 119.9 tok/s median over 101 requests | 132.5 tok/s median over 26 requests |
| GPU memory in use with both tiers loaded | 6,629 of 12,282 MiB | 6,217 of 12,282 MiB |
| Extractions (tier 0) valid | 100 / 100 | 25 / 25 |
| Schema-constrained answers (tier 1) valid | 100 / 100 | 25 / 25 |

Tier 0's extraction setup on the GPU alone (E4b, E4c): 107.4 tok/s with CUDA and 114.8 tok/s
with Vulkan, 4.3 to 4.4 GB of GPU memory, and at most 216 generated tokens per extraction against
a cap of 6,144. Speeds come from `llama-server`'s own timing logs; GPU memory from `nvidia-smi`.

On this card the Vulkan build generated about 7 to 11 percent faster than the CUDA build with this
model. Both are supported; the profile defaults to CUDA.

## What validation found, and the fixes

The first full run failed 10 checks. Every finding was fixed in the release, covered by a test,
and the whole chain was rerun. The failing runs are kept as evidence.

| # | Finding | Evidence | Fix | Test that now covers it |
|---|---|---|---|---|
| 1 | With the extraction schema unbounded, 5 of 100 extractions were cut off mid-string at the token cap and were invalid JSON (6 of 100 hit the cap). | [E4a](E4a-baseline-unbounded-schema-cuda.txt) | Every string and array in the schema has `maxLength` or `maxItems`; tier 0's cap rose to 6,144 tokens, which a test proves is enough for the largest possible extraction. | `test_extraction_schema_is_bounded` |
| 2 | 25 of 100 schema-constrained requests to the reasoner failed. Qwen3 thinks before it answers, the JSON grammar applies only after the thinking ends, and the thinking used up the token budget. | [E5a](E5a-walkthrough-cuda-run1-findings.txt) | The extractor alias turns thinking off (`reasoning: off`), and every profile starts tier 0 with `--reasoning-budget 0`. Callers that want thinking and a schema need a larger `max_tokens`; the smoke test now asks for 8,192. | `test_extractor_turns_thinking_off`, `test_tier0_never_thinks` |
| 3 | `workspace load` failed with Git's "dubious ownership" error, because the files are owned by the host user and the sandbox runs as another user. This broke every workspace step after it. | [E5a](E5a-walkthrough-cuda-run1-findings.txt) | The load and export commands mark the read-only source mount as safe in a private Git config inside the one-shot container. (Git ignores `-c` and `GIT_CONFIG_*` for the clone's upload-pack process, so the setting has to live in a config file.) | `test_workspace_commands_trust_the_read_only_mount` |
| 4 | `aegis.py status` immediately after `up` sometimes found the guard not yet listening. | [E5a](E5a-walkthrough-cuda-run1-findings.txt) | `up` waits until every container reports healthy (`--wait`, 180 s limit). | walkthrough, steps "Start" and "Status" |
| 5 | `aegis.py deny` printed "denyd". | [E5a](E5a-walkthrough-cuda-run1-findings.txt) | Fixed the message. | walkthrough, step "Deny" |
| 6 | With the workspace fixed, the second run reached the edit-test loop and found that running pytest, applying a fix, and running pytest again tripped the circuit breaker's retry rule. That rule exists to stop a model from repeating a call that already failed, but here the code had changed in between. | [E5b](E5b-walkthrough-cuda-run2-findings.txt) | Tools marked `progress` in `config/policy.yaml` (applying a patch, resetting the workspace, reindexing the vault) start a new epoch for the loop rules when they succeed with new arguments. Re-applying the same patch doesn't count. The session budget and the failure streak still span the session, and a patch doesn't reset the streak, so five failed test runs in a row still stop the agent. | `test_progress_call_lets_a_failed_check_run_again`, `test_patches_between_failing_runs_do_not_break_the_failure_streak`, `test_fixing_the_code_and_rerunning_the_same_check_is_allowed`, and five more |

## What this validation does not cover

- **The profile's intended models.** `nvidia-12gb` calls for a 4B model at Q8_0 on tier 0 and a
  9 to 14B model on tier 1. This run used Qwen3-4B Q4_K_M on both tiers, so tier 1's speed and
  memory figures are lower bounds on load, not what the recommended loadout delivers. The
  profile stays `tested = false` until someone runs its recommended models.
- **OpenCode.** The harness was driven by the smoke test and the walkthrough script over the
  same HTTP and MCP endpoints OpenCode uses. No OpenCode session was run.
- **GitHub's hosted MCP server.** Prompt injection was tested with a local fixture that serves a
  crafted issue as an untrusted `github` server. The real server was not connected.
- **Linux and Podman.** Only Windows with Docker Desktop was run on hardware. The stub-model
  smoke test runs on Linux in CI on every pull request.
- **Long sessions and concurrency.** Each check opens a fresh session; there was no multi-hour
  agent session and no parallel clients.

## Reproducing this

On a machine with the release checked out, `llama-server` installed, and a model file:

```bash
python -m pip install -r requirements-dev.txt
python aegis.py doctor --profile nvidia-12gb
docker compose build
python docs/validation/walkthrough.py --profile nvidia-12gb --label cuda --out walkthrough.txt --sample-repo <a git repo with a failing test>
```

The walkthrough expects a repository where `calc.py` defines `add(a, b)` returning `a - b` and
`test_calc.py` asserts `add(2, 3) == 5`; it applies the one-line fix. The stub-model run (E3) is
the same smoke test CI runs. Set `AEGIS_CLIENT_TOKEN` and `AEGIS_ADMIN_TOKEN` to two random
strings first, then:

```bash
docker compose -f docker-compose.yml -f tests/smoke/compose.stub-llm.yml -f tests/smoke/compose.fixtures.yml up -d --build --wait
```

```bash
python tests/smoke/smoke.py --client-token "$AEGIS_CLIENT_TOKEN" --admin-token "$AEGIS_ADMIN_TOKEN" --compose "docker compose -f docker-compose.yml -f tests/smoke/compose.stub-llm.yml -f tests/smoke/compose.fixtures.yml"
```
