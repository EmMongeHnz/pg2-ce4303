# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

AstreaView has finished the software side of **M0** (viability spikes and repo skeleton). Spikes 0a (Record3D over USB) and 0b (ArUco ring pose) passed on real hardware. The remaining hardware-bound checks (raylib DRM fps on the Pi 3, reflection test, skeleton build on worker B and the Pi) are pending; their steps and results live in `docs/m0/spike-report.md` (local only, `docs/` is not versioned).

- `especificacion.md`: the course assignment (Principios de SO, Proyecto Grupal 2). It defines the four required areas: a custom kernel driver (GPIO preferred), hardware with movement or interesting processing, distributed processing on ≥3 nodes (no shared directories), and encryption of all network traffic. It also defines the IEEE-Trans documentation sections and the required diagrams (architecture, components, sequence, features).
- `plan/implementation-plan.md`: the approved design and the source of truth for architecture, planned repo layout, protocol, phases and acceptance criteria. Read it before proposing or writing code.
- `plan/milestones-and-actions.md`: milestones M0–M8 with their tasks (`Mn.k`), dependencies and acceptance criteria. `plan/` holds only these two files.
- `docs/`: only what the user explicitly asks for (reports, guides, diagrams such as `docs/block-diagram.dot` → `docs/block-diagram.pdf`). It is git-ignored: never create, edit or track anything there unless asked, and add files to the repo only on demand (`git add -f`).

Pending inputs: delivery date and team size.

## Commands

```
uv sync --extra record3d --extra reconstruction
                                          # Python deps; always pass the extras you use, a plain `uv sync` removes them
uv run pytest                             # unit tests (tests/unit)
uv run astrea-stream-viewer               # live RGB + depth view of the Record3D USB stream (q or Ctrl+C to quit; --ring adds the ArUco pose overlay)
uv run astrea-capture --name <scan>        # scan capture: ArUco pose, keyframe every ~10°, session in out/scans/<scan>/ (--r3d replays an export)
uv run astrea-reconstruct out/scans/<scan> # preprocess + TSDF + meshing + LODs; writes model/lod-{full,high,low}.glb and report.json (--refine-poses turntable|azimuth|icp, --voxel-mm)
cmake -S renderer -B renderer/build -DCMAKE_BUILD_TYPE=Release && cmake --build renderer/build -j
                                          # GLBs from astrea-reconstruct are z-up: run the renderer with --up-axis z (--rotate-z <deg> spins about that axis)
                                          # add -DRENDERER_PLATFORM=DRM on the Pi, -DOPENGL_VERSION="ES 2.0" to test GLES2 shaders on a PC
make -C driver/holo-io                    # kernel module, needs kernel headers (Pi)
dot -Tpdf docs/block-diagram.dot -o docs/block-diagram.pdf
```

Spike scripts live in `tests/spikes/<spike>/` and are run with `uv run <script> --help`. The M3 synthetic test is `tests/spikes/m3-synthetic/` (`fetch-bunny.py` once, then `run-bunny-test.py`); the bunny is downloaded to `out/datasets/` and never versioned, and must be credited to the Stanford Computer Graphics Laboratory.

Generated files are not versioned (see `.gitignore`): `out/`, build directories, `renderer/models/*.glb` (regenerate with `tests/spikes/0c-renderer/make-test-glb.py`), `*.r3d`, `.vscode/settings.json` and private keys under `pki/`. Example certificates for the delivery are the only exception and are added explicitly (see M2.8 in `plan/milestones-and-actions.md`).

Keep `plan/milestones-and-actions.md` current: when a task or acceptance criterion is completed or partially advanced, tick its checkbox or annotate its status in the same change that advances it. Do not tick a criterion that still depends on pending hardware checks.

Always edit the `.dot` source, never the PDF. The N1–N4 / H1–H3 edge tags are defined in the diagram legend and must stay consistent with the protocol messages in `plan/implementation-plan.md`.

## Code layout

- Python is a single uv project (`pyproject.toml` at the root). Each component keeps its package under `<component>/src/<package>/` and is mapped in `[tool.setuptools.package-dir]`: `astrea_common`, `capture_service`, `coordinator`, `worker`, `device_agent`.
- The TSDF core (`astrea_common.tsdf`, NumPy only) is shared by the workers and the coordinator and works in millimetres in the plate frame; meshing, post-processing (Taubin, quadric decimation with `fast-simplification`, vertex colour, GLB + zstd) and metrics (`scikit-image`, `scipy`, extra `reconstruction`) live in `coordinator`; zstd helpers for network payloads are in `astrea_common.compression`. Integration uses per-observation weights (incidence angle and depth, computed per view) with no cap, and colour is a float32 weighted mean, so partial volumes still merge exactly.
- The ArUco ring geometry (`astrea_common.aruco_ring.RingLayout`) is shared by the generator and the pose estimator; never duplicate it.
- `renderer/` fetches raylib 5.5 with CMake FetchContent. Shaders are written once with macros for GLSL 330 and GLSL ES 100.

## Architecture (planned)

Three processing nodes that talk only over TCP with TLS 1.3 mutual auth (own CA, one cert per node, ChaCha20-Poly1305 preferred because the Pi 3 has no AES acceleration):

1. **Host PC**: `capture-service` (Record3D RGB-D over USB, pose from an ArUco ring on a manual turntable, automatic keyframes every ~10°), `coordinator` (splits views across workers, exact weighted TSDF merge, meshing, LOD export to GLB), a `worker`, and `renderer --mode viewer`.
2. **Worker B** (second team laptop): `worker` (partial TSDF fusion).
3. **Raspberry Pi 3**: `holo-io` kernel module exposing `/dev/holo-input` (GPIO joystick; optional ZX gesture sensor and `/dev/turntable` later), `device-agent` (Python, mTLS client), a light `worker`, and `renderer --mode hologram` (DRM/GLES2) feeding a Pepper's Ghost display.

Key cross-cutting rules from the plan:

- **The host is the source of truth for camera state.** It sends `CameraState` (yaw, pitch, zoom, modelId) to the Pi, and the Pi renders the model it received once. Video is never streamed.
- **Distributed reconstruction must match single-node output.** The merge is `D = Σ wᵢ·dᵢ / Σ wᵢ` on a fixed common voxel grid.
- **Wire framing** is `[u32 length][u8 type][msgpack payload]`. The renderer talks to its local Python process over a Unix socket, which has no encryption because it never touches the network.
- `/dev/holo-input` is the single input interface for every device. New sources add a `source` value to `struct HoloEvent`, so the agent never needs a new device node.
- Optional work (motorized turntable, ZX sensor) is phase 8. Its interfaces exist from the start but stay disabled.

## General coding preferences

### Language
- Everything related to source code (including comments, filenames, etc) must be in english, external docs might be in a different language, ask user if unclear and save the preference here or in memory.

### Code conventions
- If a project does not specify a local naming convention, it must follow:
    - `UPPER_CASE` for constants, defines, enum, and similar.
    - `camelCase` for variables, function arguments and similar.
    - `kebab-case` for filenames, folders, CLI parameters and similar.
    - `PascalCase` for function or object names.
- Local exceptions forced by the toolchain: importable Python modules and packages use `snake_case` filenames (hyphens cannot be imported) and tests follow pytest's `test_*.py`; CLI scripts that are not imported stay kebab-case. Kernel code in `driver/` follows the Linux kernel coding style (`snake_case` functions, tabs).

### Comments
- Don't write comments that explain what or how the code works. If code needs explaining, refactor: rename, extract a function, or simplify the logic.
- Extract complex blocks into functions named after what the comment would have said.
- Only comment the why, when it can't be expressed in code: non-obvious business rules, workarounds for third-party bugs, or performance decisions that look wrong but aren't.
- API contracts, architecture decisions, and onboarding guides belong in external docs or declaration-level docstrings, not inline comments.
- The only "general comment" allowed is to add headers in files to briefly describe its contents or usage (for CLI's for example).

## Project conventions

- The IEEE paper and the planning documents are in Spanish.
- Keep the `holo-*` identifiers (`holo-io`, `/dev/holo-input`, `HoloEvent`, `--mode hologram`) as they are. They describe the hologram/IO function, not the old project name.
- Phases go risk-first: run the viability spikes (phase 0) before building the components that depend on them.
