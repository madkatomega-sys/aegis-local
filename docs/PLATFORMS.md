# Platform setup

Aegis-Local runs the same way on Windows 11 and Linux. The only per-OS differences are how you
install the inference engine and the container runtime. After that, every command and config file
is identical.

## Requirements

| | Windows 11 | Linux |
|---|---|---|
| Python | 3.11 or newer (python.org installer or `winget install Python.Python.3.12`) | 3.11 or newer (distribution package) |
| Containers | Docker Desktop (WSL2 backend) or Podman Desktop | Docker Engine or Podman (rootless works) |
| Inference | `llama-server` from a llama.cpp release build for your GPU | `llama-server` from a release build, your package manager, or a source build |
| Frontend | OpenCode (`npm install -g opencode-ai`) | Same |
| Git | Git for Windows | Distribution package |

## Install llama.cpp

Download the release build that matches your GPU from the llama.cpp releases page, or build it.

| GPU | Windows build | Linux build |
|---|---|---|
| NVIDIA | CUDA build (and the matching CUDA runtime DLLs) | CUDA build |
| AMD discrete | HIP (ROCm) build, or Vulkan | ROCm (HIP) or Vulkan build |
| AMD unified-memory APU | HIP or Vulkan build | Vulkan (RADV) or ROCm build |
| Intel Arc | SYCL build, or Vulkan | SYCL (oneAPI) or Vulkan build |
| None | CPU build | CPU build |

Then tell Aegis where it is, either on `PATH` or in `.env`:

```text
AEGIS_LLAMA_SERVER=C:\tools\llama.cpp\llama-server.exe     # Windows
AEGIS_LLAMA_SERVER=/home/you/.local/bin/llama-server       # Linux
```

## First run

```bash
git clone https://github.com/madkatomega-sys/aegis-local.git
cd aegis-local
cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env
python aegis.py doctor
python aegis.py up --profile nvidia-12gb
```

`doctor` checks Python, the container runtime, the `llama-server` binary, the selected profile,
and that model files exist. It changes nothing.

## Verified configurations

Configurations where the whole release was run on real hardware, with the evidence. A profile
is marked `tested = true` only once its recommended models have been run; until then, these
results show that the harness works on the hardware, not how fast the recommended loadout is.

| Release | OS and runtime | Hardware | Profile and backend | Models | Result |
|---|---|---|---|---|---|
| 0.1.0 | Windows 11 Pro 26300, Docker Desktop 29.9.3 | i9-9900K, 32 GB RAM, RTX 4070 12 GB | `nvidia-12gb`, llama.cpp b12361 CUDA 13.5 and Vulkan | Qwen3-4B Q4_K_M on both tiers | All checks pass; tier 1 at 120 tok/s (CUDA) and 133 tok/s (Vulkan), tier 0 on the CPU at 12 tok/s, 6.6 GB GPU memory. [Evidence](validation/0.1.0-windows11-rtx4070/README.md) |

Every pull request also runs the full stack with a stub model on Linux (Ubuntu, Docker) in CI.
To add a row, run `docs/validation/walkthrough.py` on your machine and open a pull request with
its transcript under `docs/validation/`.

## Windows notes

- Clone with LF line endings. The repository's `.gitattributes` handles this, but if you cloned
  before it existed, commit or stash your work and then run `git add --renormalize .`.
- Keep your projects on the Windows filesystem. `aegis workspace load` copies them into a container
  volume, so container tools don't pay the WSL2 cross-filesystem cost.
- Docker Desktop's `host.docker.internal` reaches services bound to `127.0.0.1` on Windows, so
  `llama-server` stays on loopback.
- If you use Podman Desktop and containers can't reach the host, enable WSL mirrored networking
  (`networkingMode=mirrored` in `%USERPROFILE%\.wslconfig`) and restart WSL. This path is still
  being verified; see the open questions in [PLAN.md](PLAN.md#11-risks-and-open-questions).

## Linux notes

- On Linux, containers reach the host through the bridge address, not `127.0.0.1`. `aegis.py`
  binds `llama-server` to the Docker bridge gateway (for example `172.17.0.1`), which is reachable
  from the host and from containers but not from the LAN. Override with `AEGIS_LLAMA_HOST`.
- Rootless Podman with `pasta` networking may need `AEGIS_LLAMA_HOST` set by hand. Reports
  welcome.
- For AMD unified-memory systems, raise the GPU memory allocation (the `amdgpu.gttsize` kernel
  parameter, or the BIOS UMA setting) before loading large MoE models.
