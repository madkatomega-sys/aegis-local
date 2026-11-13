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
