# AstreaView

Escaneo 3D distribuido con visualización holográfica (Proyecto Grupal 2, Principios de Sistemas Operativos).

Un iPhone con TrueDepth captura el objeto con Record3D sobre una base giratoria con un anillo ArUco; la reconstrucción TSDF se reparte entre tres nodos conectados con mTLS; el modelo se ve en la PC y en un display Pepper's Ghost controlado por una Raspberry Pi 3 con un driver de kernel propio.

- Diseño: [`plan/implementation-plan.md`](plan/implementation-plan.md)
- Milestones y acciones: [`plan/milestones-and-actions.md`](plan/milestones-and-actions.md)
- Enunciado: [`especificacion.md`](especificacion.md)

## Estructura

| Carpeta | Contenido | Hito |
|---|---|---|
| `common/` | Tipos compartidos (`RgbdFrame`), geometría del anillo ArUco, escritor GLB; luego protocolo y TLS | M0, M2 |
| `host/capture-service/` | Ingesta Record3D (`.r3d` y USB), pose ArUco, renders sintéticos | M0, M3 |
| `host/coordinator/` | Reparto, merge TSDF, mallado y LOD (esqueleto) | M3, M4 |
| `worker/` | Fusión TSDF parcial (esqueleto) | M3, M4 |
| `device-agent/` | Agente de la Pi (esqueleto) | M5 |
| `renderer/` | Visor C + raylib, modos `viewer` y `hologram` | M0, M5 |
| `driver/holo-io/` | Módulo de kernel `/dev/holo-input` (esqueleto) | M1 |
| `pki/` | CA y certificados por nodo | M2 |
| `hardware/` | Generador del anillo ArUco | M0 |
| `tests/unit/` | Pruebas de `pytest` | — |
| `tests/spikes/` | Scripts de los spikes de M0 | M0 |
| `plan/` | Plan de implementación y milestones | — |
| `docs/` | Solo lo que se pida explícitamente; no se versiona por defecto | — |

## Compilar y probar

Requisitos: Python ≥ 3.11 con [uv](https://docs.astral.sh/uv/), CMake ≥ 3.16 y un compilador de C.

```bash
uv sync                          # dependencias de Python (añadir --extra record3d para el streaming USB)
uv run pytest                    # pruebas unitarias

uv run tests/spikes/0c-renderer/make-test-glb.py renderer/models/lod-low.glb --triangles 8000
uv run tests/spikes/0c-renderer/make-test-glb.py renderer/models/lod-high.glb --triangles 30000
cd renderer
cmake -B build -DCMAKE_BUILD_TYPE=Release   # en la Pi: -DRENDERER_PLATFORM=DRM
cmake --build build -j
./build/renderer --mode viewer
```

En Linux de escritorio, raylib necesita `libx11-dev libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev libgl1-mesa-dev`; en la Pi, `libdrm-dev libgbm-dev libegl-dev libgles2-mesa-dev`.

Driver (solo en la Pi, con los headers del kernel instalados):

```bash
cd driver/holo-io && make
```

## Spikes de M0

```bash
uv run hardware/aruco-ring/generate-ring.py --out-dir hardware/aruco-ring/out    # anillo imprimible
uv run tests/spikes/0b-ring-pose/synthetic-sweep.py --out-dir out/0b-sweep        # precisión de la pose
uv run tests/spikes/0a-record3d/make-synthetic-r3d.py out/synthetic.r3d          # captura de prueba
uv run tests/spikes/0b-ring-pose/track-ring.py --r3d out/synthetic.r3d           # tracking sobre RGB-D
```

Los pasos con el iPhone, la Pi y la maqueta están en `docs/m0/spike-report.md` (local, no versionado).
