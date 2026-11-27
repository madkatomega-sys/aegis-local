# Setup guide: from a fresh machine to OpenCode's browser interface

This guide takes you from nothing to working on your own repository in OpenCode's browser
interface, with local models doing the work and the Aegis guard between the agent and your code.
Plan for an hour, most of it downloads.

By the end you'll have:

- two local models running on your machine (no cloud account needed);
- the Aegis stack in containers: the model router, the guard, and the sandboxed workers;
- OpenCode's browser interface on `http://127.0.0.1:4096`, using only those models, with every
  file change going through the guard into a sandboxed copy of your repository.

The steps are the same on Windows 11 and Linux. Where they differ, both are shown.
[PLATFORMS.md](PLATFORMS.md) has more detail on each platform.

## 1. Check your machine

You need:
- 32 GB of RAM is comfortable; 16 GB works with the smaller profiles.
- About 30 GB of free disk for the container images and two model files.
- A GPU is optional (there is a CPU-only profile), but a coding agent is much faster with one.

Once you have Python and the repository ([sections 2](#2-install-the-prerequisites) and
[3](#3-get-aegis-local)), Aegis can do this step for you:

```bash
python aegis.py models suggest
```

It reads the GPU (through `nvidia-smi`, the Windows registry, or `/sys` and `lspci` on Linux) and
the system RAM, then names a profile and the two model files that go with it. Check what it found
against the table below; if it can't read your GPU's memory, it says so and suggests `cpu-only`.

To find your GPU and its memory by hand:

| | Windows | Linux |
|---|---|---|
| NVIDIA | `nvidia-smi --query-gpu=name,memory.total --format=csv` | the same |
| AMD, Intel, any | Task Manager > Performance > GPU: the name, and "Dedicated GPU memory" | `lspci \| grep -i -e vga -e 3d`; then `rocm-smi` (AMD) or your vendor's tool for memory |

Then pick the profile in `config/profiles/` that matches (`python aegis.py profiles` lists them):

| Your GPU | Profile | Backend |
|---|---|---|
| NVIDIA with 8 GB | `nvidia-8gb` | CUDA |
| NVIDIA with 12 GB (RTX 3060 12 GB, 4070, 5070) | `nvidia-12gb` | CUDA |
| NVIDIA with 16 GB | `nvidia-16gb` | CUDA |
| NVIDIA with 24 GB or more | `nvidia-24gb-plus` | CUDA |
| AMD with 16 GB / 24 GB | `amd-16gb` / `amd-24gb` | HIP (ROCm) or Vulkan |
| AMD unified-memory APU (Ryzen AI Max) | `amd-unified-128gb` | Vulkan or ROCm |
| Intel Arc 12 GB / 16 GB | `intel-arc-12gb` / `intel-arc-16gb` | SYCL or Vulkan |
| No usable GPU | `cpu-only` | CPU |

Three profiles have measured results so far, all on one RTX 4070 machine:

| Profile | Fixed in the eval suite | Tier 1 speed | Evidence |
|---|---|---|---|
| `nvidia-12gb` | 14/15 | about 51 tokens/s | [12 GB](validation/12gb-tier1-2026-11-17-windows11-rtx4070/README.md) |
| `nvidia-8gb` | 17/18 | about 49 tokens/s; the tiers take about 6.1 GB of GPU memory | [8 GB and CPU](validation/8gb-and-cpu-2026-11-27-windows11-rtx4070/README.md) |
| `cpu-only` | 12/12, about four times slower per run | about 4 tokens/s | [8 GB and CPU](validation/8gb-and-cpu-2026-11-27-windows11-rtx4070/README.md) |

The 8 GB numbers come from a 12 GB card; on a real 8 GB card, the desktop gets about 1.9 GB, so
close heavy programs. The other profiles are reasoned starting points: they should work, but
nobody has reported numbers yet. All stay `tested = false` until a second person reproduces them.

## 2. Install the prerequisites

| What | Windows 11 | Linux |
|---|---|---|
| Python 3.11+ | python.org, or `winget install Python.Python.3.12` | your distribution's package |
| Git | Git for Windows | your distribution's package |
| Containers | Docker Desktop (WSL2 backend) | Docker Engine, or Podman |
| llama.cpp | a release build for your backend (below) | a release build, a package, or a source build |
| OpenCode 1.19.5 | the release asset for your OS (below) | the same |

**llama.cpp.** From the [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases), take
the build for your profile's backend: CUDA (on Windows, also the matching CUDA runtime archive),
HIP/ROCm, SYCL, Vulkan, or CPU. Unpack it anywhere. Aegis was validated with build b12361.

**OpenCode.** Use **1.19.5**. That is the version the browser interface and the Aegis config were
checked with; see [section 8](#8-the-opencode-desktop-app-and-other-clients) for 2.x. Download the
asset for your OS from the [v1.19.5 release](https://github.com/anomalyco/opencode/releases/tag/v1.19.5),
unpack it, and put the folder holding `opencode` (or `opencode.exe`) on your `PATH`. Check it:

```bash
opencode --version
```

It should print `1.19.5`.

## 3. Get Aegis-Local

```bash
git clone --branch v0.3.0 https://github.com/madkatomega-sys/aegis-local.git
cd aegis-local
cp .env.example .env
```

On Windows PowerShell, the last line is `Copy-Item .env.example .env`.

This guide is for release 0.3.0, the first with the model helper, the browser launcher
(`aegis.py opencode --web`), and per-chat guard sessions.

In `.env`, set your profile and where llama-server is:

```text
AEGIS_PROFILE=nvidia-12gb
AEGIS_LLAMA_SERVER=C:\tools\llama.cpp\llama-server.exe     # Windows
AEGIS_LLAMA_SERVER=/home/you/llama.cpp/llama-server        # Linux
```

## 4. Gather the models

Each profile runs two models:
- **Tier 0**, small, often on the CPU: it extracts facts from untrusted content and writes titles.
- **Tier 1**, the reasoner: the model the agents actually think with.

### The quick way

```bash
python aegis.py models get
```

For your profile (from `.env`, or the suggested one; `--profile` picks another), this:
1. shows the two files, their size and license, and where they come from;
2. checks there is room on the disk, and asks before downloading anything;
3. downloads from Hugging Face into `models/` in the checkout (ignored by Git; `--dir` puts them
   elsewhere), resuming a download that was cut off;
4. checks each file's SHA-256 against [config/models.toml](../config/models.toml), and deletes a file
   that doesn't match;
5. writes `AEGIS_PROFILE`, `AEGIS_TIER0_MODEL` and `AEGIS_TIER1_MODEL` into `.env`.

Files already there are hash-checked, not downloaded again. `--tier0` and `--tier1` swap in another
model from the catalog (`python aegis.py models list` shows them all, and which were measured).
`--only tier0` (or `tier1`) fetches and sets just that tier's file. `--max-gb N` refuses, before
downloading anything, if the files still missing add up to more than `N` GB.
`python aegis.py models verify` re-checks the files `.env` points at.

`config/models.toml` pins every file by repository, name, size and SHA-256, so a file that changes
upstream is refused rather than run. Read each model's license before you use it; all the files
in the catalog today are Apache-2.0.

### By hand

Open your profile's file in `config/profiles/`. `size_class` and `quant` under `[tiers.tier0]` and
`[tiers.tier1]` say what to get, `candidates` names models that fit, and `download` is the catalog
entry `models get` would use. [MODEL-LOADOUTS.md](MODEL-LOADOUTS.md) explains the choices.

For each tier:

1. **Find a GGUF build** of a candidate on [Hugging Face](https://huggingface.co): search for the
   model name plus "GGUF". Prefer the model maker's own repository; otherwise use a well-known
   repackager.
2. **Pick the file** with the profile's quantization in its name, for example `Q6_K` or `Q4_K_M`.
3. **Check the license** on the base model's page, and note the file's SHA-256: Hugging Face shows
   it on the file's page.
4. **Download it** to a folder outside the repository (model files never belong in Git), and check
   the hash:

   ```bash
   sha256sum Qwen3.5-9B-Q6_K.gguf                    # Linux
   Get-FileHash Qwen3.5-9B-Q6_K.gguf -Algorithm SHA256   # Windows PowerShell
   ```

5. **Point `.env` at the files:**

   ```text
   AEGIS_TIER0_MODEL=C:\models\Qwen3-4B-Q4_K_M.gguf
   AEGIS_TIER1_MODEL=C:\models\Qwen3.5-9B-Q6_K.gguf
   ```

**What the 12 GB profile was measured with**, on an RTX 4070:

| Tier | File | From | SHA-256 | License |
|---|---|---|---|---|
| 1 | `Qwen3.5-9B-Q6_K.gguf` (7.36 GB) | `lmstudio-community/Qwen3.5-9B-GGUF` | `b2ccc477f0a2449de299bef560b63e498cae94a2f3a3bae13df01168d77773e6` | Apache-2.0 |
| 0 | `Qwen3-4B-Q4_K_M.gguf` (2.50 GB) | `Qwen/Qwen3-4B-GGUF` | `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5` | Apache-2.0 |

The 9B on tier 1 fixed 14 of 15 eval runs, against 10 of 19 with a 4B on tier 1. If your GPU has
less memory than the profile assumes, use a smaller quantization (for example `Q4_K_M`) before you
drop to a smaller model.

## 5. Start the stack

```bash
python aegis.py doctor
python aegis.py up
python aegis.py status
```

- **`doctor`** checks Python, the container runtime, llama-server, the profile, and the model
  files. Fix anything it marks before going on.
- **`up`** starts both llama-server tiers and the containers.
  - **On a release tag:** `up --pull` uses that release's published, signed images (see
    [SECURITY.md](../SECURITY.md) to verify them).
  - **Without `--pull`,** it builds the images from your checkout, which takes several minutes the
    first time. Always do this on `main`: `--pull` would run the last release's images, which may
    not match `main`'s config and plugins.
- **`status`** should show both tiers healthy, the router, and the guard.

The first start takes a few minutes while the models load.

## 6. Load your repository into the sandbox

The agent never touches your checkout. It works on a copy in a container volume, and its changes
come back as a patch you review.

```bash
python aegis.py workspace load ../my-project
python aegis.py workspace prepare
```

- **`load`** copies your repository's **committed** HEAD. Commit or stash your work first; it
  doesn't copy uncommitted changes.
- **`prepare`** is only needed if your tests import packages. It installs what `requirements*.txt`
  and `pyproject.toml` declare, as wheels only. See
  [the code worker's README](../services/sandboxes/code-worker/README.md#project-dependencies-workspace-prepare)
  for what it accepts and why.

## 7. Open OpenCode's browser interface

```bash
python aegis.py opencode --web ../my-project
```

It prints the address and a password:

```text
OpenCode 1.19.5 browser interface: http://127.0.0.1:4096/
sign in as user `opencode` with password ...   (also in .aegis/tokens.env)
```

Your browser opens at that address (`--no-browser` leaves it closed). Sign in as `opencode` with
that password. The server listens only on `127.0.0.1`, and the password keeps other programs on
your machine out. Stop it with Ctrl+C. `--port` picks another port.

The address you open is an origin check in front of OpenCode, which itself listens on a spare
port (the launcher prints which). The check refuses requests that other web pages in your
browser could send there. Always use the address the launcher prints, not OpenCode's own port.

What the command sets up for you:
- OpenCode uses the Aegis config: only the local models, and only the agents below. Its own file,
  shell, and web tools are off, so every action goes through the guard.
- The Aegis plugins are loaded. One tags each tool call with its chat, so each chat gets its own
  breaker, call budget, and untrusted-content flag in the guard.
- A private OpenCode data folder under `.aegis/opencode-web`, separate from any other OpenCode
  setup on the machine.

**Working in it.** Pick the agent in the box under the prompt:

| Agent | Can do |
|---|---|
| Planner (default) | Read the sandbox and run checks; plans, can't change files |
| Builder | Change files in the sandbox, run tests, linters and type checks |
| Essayist | Write prose with checked citations from your notes vault |

A typical task: ask the Planner what's wrong, then switch to the Builder and ask it to fix it and
show the tests pass. When you're happy, export the change and apply it yourself:

```bash
python aegis.py workspace export
git -C ../my-project apply ../aegis-local/exports/aegis-<time>.patch
```

**If something is held for approval.** When a chat has read untrusted content (GitHub, if you've
enabled it), the guard holds its next change and the agent tells you. Decide from a terminal:

```bash
python aegis.py pending
python aegis.py approve <id>
python aegis.py deny <id>
```

**If the circuit breaker trips.** The agent repeated a failing call or looped. The chat's tools stop
until you look. `python aegis.py status` shows the session, and `python aegis.py reset <session>`
resumes it. A new chat starts with a clean breaker.

The terminal interface works the same way: `python aegis.py opencode ../my-project`.

## 8. The OpenCode desktop app and other clients

**The OpenCode desktop app (2.3.0) is experimental with Aegis.** The browser interface above is the
supported way. The app can work once it's started with the right environment (below), but it has
no status display, and it hasn't been through the release checks.

In 0.3 every request through the app seemed to hang. The
[cause](validation/opencode2-service-2026-11-27-windows11/README.md) is that the app starts
OpenCode's background service itself, and the service reads `AEGIS_CLIENT_TOKEN` from its own
environment, not from yours. Without the token the guard refuses the connection, the agent has no
Aegis tools, and it keeps trying without finishing.

To make the app see the Aegis config and the token, they have to be in the environment the app
starts with. On Windows that means user environment variables, set once in PowerShell:

```powershell
python aegis.py env    # prints the three values for this checkout
[Environment]::SetEnvironmentVariable("OPENCODE_CONFIG", "<value printed above>", "User")
[Environment]::SetEnvironmentVariable("OPENCODE_CONFIG_DIR", "<value printed above>", "User")
[Environment]::SetEnvironmentVariable("AEGIS_CLIENT_TOKEN", "<value printed above>", "User")
```

Then quit the app completely, including its background `opencode-cli` process (check Task
Manager), and start it again. Undo it by setting each variable to `$null` the same way.

What you get under 2.x ([evidence](validation/opencode2-plugins-2026-11-27-windows11/README.md)):
- **Per-chat guard sessions.** The Aegis plugins serve both OpenCode plugin APIs. A forked chat
  shares its source's guard session, and a subagent its parent's.
- **The Aegis lockdown, including 2.x's code mode.** The code-mode tool `execute` runs the agents'
  JavaScript. Inside it, `fetch` is refused, because the config denies `webfetch`. OpenCode's own
  code-mode tools, such as creating Git worktrees, are turned off by name.
- **No status display.** 2.x shows toasts only from GUI and TUI plugins, and Aegis doesn't have
  one yet. Use `python aegis.py status` and `python aegis.py pending`.

The token in a user environment variable can be read by any program you run, as
`.aegis/tokens.env` can.

**Adding Aegis as "just a provider" gives you the models without the protection.** Any client that
takes an OpenAI-compatible endpoint can use the router, including the desktop app's
Settings > Providers > Connect custom provider:

| Field | Value |
|---|---|
| Base URL | `http://127.0.0.1:4000/v1` |
| API key | any non-empty text; the router is loopback-only and doesn't check it |
| Models | `aegis-reasoner` (tier 1), `aegis-fast` (tier 0) |

But that's only the models. The client's own tools (shell, file edits, web access) still act
directly on your machine, without the guard, the sandbox, or approvals. Use it for chat. For work
on code, use `python aegis.py opencode` or `--web`, which load the full Aegis config.

## 9. Check that it works

The eval suite runs the Builder on small broken projects through everything you just set up, and
checks the results with the projects' own tests:

```bash
python evals/run.py run --fixtures calc,pace --runs 1
```

Both should come back `fixed` in about a minute each. If they do, the models, router, guard,
sandbox, `workspace prepare`, and OpenCode are all working. See [evals/README.md](../evals/README.md)
for more.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `doctor` can't find llama-server or a model | Check the paths in `.env`; use full paths |
| A tier never becomes healthy | Read `.aegis/logs/tier0.log` or `tier1.log`; usually the model doesn't fit in GPU memory: use a smaller quantization |
| `up` says the container stack failed | `docker compose logs`; the guard refuses to start without the tokens `aegis.py` generates, so start it with `aegis.py up`, not plain `docker compose` |
| `--web` refuses your OpenCode | It needs 1.x; install 1.19.5 as in step 2 |
| The browser keeps asking for a password | Use user `opencode` and the password in `.aegis/tokens.env` (`OPENCODE_SERVER_PASSWORD`) |
| The agent says the workspace is empty | Run `python aegis.py workspace load <path>` first |
| Tests fail with `ModuleNotFoundError` | Run `python aegis.py workspace prepare` |
| On Linux, containers can't reach the models | See the Linux notes in [PLATFORMS.md](PLATFORMS.md#linux-notes) |
| Anything else | `python aegis.py status`, then the guard's view: every call is in its audit ledger |

Not verified yet: a native Linux run on real hardware, and the GitHub upstream with a real token.
Both should work as described, but nobody has checked them on hardware; see
[PLAN.md](PLAN.md#9-milestones-and-acceptance-criteria).
