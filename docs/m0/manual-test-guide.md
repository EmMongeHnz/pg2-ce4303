# Guía rápida de prueba manual (M0, sin hardware)

Unos 15 minutos. Todo se corre desde la terminal integrada de VS Code (`Ctrl+ñ` o *Terminal → New Terminal*) con la carpeta del repo abierta.

## 0. Requisitos

| Herramienta | Para qué | Instalación |
|---|---|---|
| Python ≥ 3.11 | Spikes 0a/0b | python.org o el gestor del sistema |
| uv | Entorno y dependencias | `pip install uv` o https://docs.astral.sh/uv/ |
| CMake ≥ 3.16 + compilador de C | Renderer | Linux: `sudo apt install build-essential cmake`; Windows: Visual Studio Build Tools (C++) + CMake |
| Git | CMake descarga raylib con git | — |

En Linux, raylib necesita además:

```bash
sudo apt install libx11-dev libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev libgl1-mesa-dev
```

> Recomendado: Linux o WSL2. En Windows nativo también funciona, pero `pyliblzfse` puede necesitar compilarse con las Build Tools de C++.

Extensiones útiles de VS Code: *Python*, *CMake Tools*, *C/C++*.

## 1. Instalar y correr las pruebas

```bash
uv sync
uv run pytest
```

**Esperado:** `46 passed`.

En VS Code también se pueden correr desde la pestaña *Testing*: elegir el intérprete `.venv` (*Python: Select Interpreter*) y configurar pytest con la carpeta `tests/unit`.

## 2. Generar el anillo ArUco

```bash
uv run hardware/aruco-ring/generate-ring.py --out-dir out/ring
```

**Esperado:** `out/ring/aruco-ring.pdf`, `aruco-ring.png` y `ring-layout.json`. Abrir el PNG en VS Code: 12 marcadores alrededor del plato, marcas cada 10° y una barra de 50 mm abajo.

## 3. Spike 0b: precisión de la pose con vistas sintéticas

```bash
uv run tests/spikes/0b-ring-pose/synthetic-sweep.py --out-dir out/0b-sweep
```

**Esperado (≈10 s):** una tabla en la terminal con 431/432 vistas con pose y un error de azimut con mediana ≈ 0.05°. En `out/0b-sweep/` quedan `summary.md`, `sweep.csv` y `sample-0..2.png` con los marcadores detectados y los ejes del plato dibujados.

Variante sin manos tapando el anillo (todas las vistas deben quedar bajo 1°):

```bash
uv run tests/spikes/0b-ring-pose/synthetic-sweep.py --out-dir out/0b-clean --hands 0
```

## 4. Spike 0a: leer un `.r3d` (sintético)

```bash
uv run tests/spikes/0a-record3d/make-synthetic-r3d.py out/synthetic.r3d
uv run tests/spikes/0a-record3d/inspect-r3d.py out/synthetic.r3d --save-dir out/0a
```

**Esperado:** 36 frames de 640×480 (RGB) y 320×240 (profundidad), con profundidad mediana ≈ 0.35 m. En `out/0a/` quedan, por frame, el RGB, una vista previa de la profundidad, un `.npy` y una nube de puntos `.ply` (se puede abrir en MeshLab o CloudCompare).

## 5. 0a + 0b juntos: tracking sobre la captura

```bash
uv run tests/spikes/0b-ring-pose/track-ring.py --r3d out/synthetic.r3d
```

**Esperado:** una línea por frame con el azimut, que avanza de 10° en 10° (0, 10, 20…), y al final `36/36 frames with pose` y un residuo profundidad-vs-pose de ≈ 0.5 mm. Ese residuo es la verificación de que las intrínsecas y la profundidad concuerdan.

## 6. Spike 0c: renderer en la PC

```bash
uv run tests/spikes/0c-renderer/make-test-glb.py renderer/models/lod-low.glb --triangles 8000
uv run tests/spikes/0c-renderer/make-test-glb.py renderer/models/lod-high.glb --triangles 30000
cmake -S renderer -B renderer/build -DCMAKE_BUILD_TYPE=Release
cmake --build renderer/build -j --config Release
```

La primera configuración descarga raylib (necesita internet). En Windows con Visual Studio, el ejecutable queda en `renderer/build/Release/renderer.exe`.

Ejecutar **desde la carpeta `renderer/`** (busca los modelos en `models/`):

```bash
cd renderer
./build/renderer --mode viewer
```

- Arrastrar con el mouse: rotar. Rueda o `+`/`-`: zoom. Flechas: yaw/pitch. `R`: reset. `Esc`: salir.
- **Esperado:** una figura irregular con colores (azul arriba, salmón abajo) y el contador de triángulos y fps en la esquina.

Modo holograma (fondo negro, espejado y autorrotación):

```bash
./build/renderer --mode hologram --lod low --width 1280 --height 720
```

Benchmark (lo mismo que se correrá en la Pi):

```bash
./build/renderer --mode hologram --lod low --width 1280 --height 720 --benchmark 10
```

**Esperado:** dos líneas `benchmark:` con los fps promedio y el 1 % low. En una PC cualquiera debería pasar holgadamente de 30 fps.

## 7. (Opcional) Driver esqueleto

Solo en Linux con los headers del kernel (`sudo apt install linux-headers-$(uname -r)`):

```bash
make -C driver/holo-io
sudo insmod driver/holo-io/holo-io.ko && sudo dmesg | tail -2 && sudo rmmod holo-io
make -C driver/holo-io clean
```

**Esperado:** `holo-io: loaded (skeleton 0.0.1)` y `holo-io: unloaded` en `dmesg`.

## Si algo falla

| Síntoma | Causa probable |
|---|---|
| `uv sync` falla en `pyliblzfse` | Falta el compilador de C (Build Tools en Windows, `build-essential` en Linux) |
| CMake no encuentra X11/GL | Faltan los paquetes `-dev` del paso 0 |
| `RENDERER: model not found` | No se corrió desde `renderer/` o falta el paso de `make-test-glb.py` |
| El renderer abre y cierra en WSL | Falta WSLg (Windows 11) o un servidor X |

Las pruebas con el iPhone, la Pi y el panel están en [`spike-report.md`](spike-report.md).
