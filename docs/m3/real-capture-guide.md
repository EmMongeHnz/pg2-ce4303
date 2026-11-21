# M3 — Prueba con captura real (iPhone → GLB → renderer)

Escanea un objeto sobre el plato manual con el anillo ArUco y obtén los GLB `lod-high` y `lod-low`. Es la parte de M3 que depende de hardware.

> **Estado:** `astrea-capture` y `astrea-reconstruct` están probados con poses y vistas sintéticas. Los parámetros por defecto (radio de recorte, márgenes, umbrales de estabilidad) **no se han ajustado con capturas reales**; esta guía incluye cómo corregirlos.

## 0. Requisitos

- iPhone 12 con Record3D (streaming USB comprado), cable y `usbmuxd` en la PC (ver `docs/m0/spike-report.md`, spike 0a).
- Plato con el anillo ArUco impreso (`hardware/aruco-ring/generate-ring.py`, plato de 180 mm por defecto) y marcas cada 10°.
- Teléfono fijo en trípode o soporte, a 25–35 cm del centro del plato y algo elevado (≈ 30–40°). La TrueDepth no mide bien por debajo de ~20–25 cm.
- Objeto opaco y mate, de unos 12 cm de alto como máximo y con radio ≤ 10 cm. Debe caber en el cubo de 25 cm de la TSDF.

```bash
uv sync --extra record3d --extra reconstruction
uv run pytest                      # 108 pruebas en verde
```

## 1. Comprobar la entrada

```bash
uv run astrea-stream-viewer --ring
```

Con el objeto sobre el plato y el plato en su posición de trabajo debe verse la pose en verde (con varios marcadores usados y `reproj` baja) y `pose` cercano a 100 %. Si se pierde la pose al girar el plato, subir el teléfono o mejorar la luz. Cerrar con `q`.

## 2. Capturar

```bash
uv run astrea-capture --name cup
```

Añade `--layout <ring-layout.json>` solo si el anillo no usa los valores por defecto. Opciones útiles: `--flip-horizontal` (si no se detecta ningún marcador por imagen espejada) y `--rotate 0|90|180|270` (orientación de la vista previa).

1. Gira el plato **despacio**, unos 10° por vez, y detente ~1 s en cada posición. Se guarda un keyframe cuando la pose lleva ~0.5 s estable y el plato avanzó al menos 10° desde el anterior.
2. La ventana muestra `keyframes N` y un anillo de cobertura abajo a la derecha: verde = sector con keyframe, gris = falta, línea amarilla = posición actual. El texto se pone verde al guardar uno.
3. Da una vuelta completa (≈ 36 keyframes). Opcional: segunda pasada con el teléfono más alto con otro `--name` (por ejemplo `cup-high`).
4. Cierra con `q`. El resultado queda en `out/scans/cup/` (`manifest.json` y `views/*.npz`).

Si no se guarda ningún keyframe: confirmar que la pose aparece en verde, bajar `--stable-seconds` o subir `--max-angle-drift-deg` (por ejemplo 0.8) si el ruido de la pose es mayor al esperado.

## 3. Reconstruir

```bash
uv run astrea-reconstruct out/scans/cup
# con segunda pasada:
uv run astrea-reconstruct out/scans/cup out/scans/cup-high --out out/scans/cup/model
```

Muestra el porcentaje de profundidad que se conserva por vista, los triángulos de cada LOD frente a su presupuesto (30 000 y 8 000), el número de componentes y los tamaños de GLB (con y sin zstd). Escribe `out/scans/cup/model/lod-high.glb`, `lod-low.glb` y `report.json`. El código de salida es 1 si algún LOD excede el presupuesto o no es una sola componente (gate de M3).

### Ajustes si el resultado no es bueno

| Síntoma | Qué probar |
|---|---|
| Queda un disco o anillo del plato | Subir `--plate-margin-mm` (por defecto 4) |
| El objeto sale recortado en los lados | Subir `--max-radius-mm` (por defecto 100) o `--max-height-mm` (230) |
| «keeps under 2 %» en varias vistas | Revisar `--min-depth-mm` y `--max-depth-mm` (200–500) con la distancia real del teléfono |
| Superficie con puntos sueltos o «pelusa» | Subir `--min-support-pixels` o `--min-component-pixels` |
| Huecos en bordes finos | Subir `--support-depth-mm` (por defecto 8) |
| Varias componentes en un LOD | Revisar la pose (reproyección alta en las líneas `keyframe` de la consola de captura) y repetir la captura |

## 4. Verla en el renderer

```bash
cmake -S renderer -B renderer/build -DCMAKE_BUILD_TYPE=Release && cmake --build renderer/build -j
renderer/build/renderer --mode viewer --model out/scans/cup/model/lod-high.glb
```

La primera compilación descarga raylib (requiere red). Comprobar que el modelo carga, rota con el ratón y tiene colores. Repetir con `lod-low.glb`. En la Pi, ver spike 0c en `docs/m0/spike-report.md`.

## 5. Gate de ángulo ArUco (< 1°)

Con el plato en las marcas de 0°, 90°, 180° y 270° (y alguna intermedia), anotar el `az` que muestra `astrea-stream-viewer --ring` y calcular la diferencia contra la marca, restando el desfase de la primera posición:

| Marca real | `az` medido | Error (tras restar el desfase) |
|---|---|---|
| 0° | | |
| 90° | | |
| 180° | | |
| 270° | | |

Se cumple si todos los errores son < 1°. La tabla de azimuts también sale en la consola de `astrea-capture` (una línea `keyframe` por vista).

## 6. Registrar resultados

Anota en esta sección (y en `docs/milestones.md`, sin marcar el gate hasta tener los datos): objeto, número de keyframes, sectores cubiertos, fps, `report.json` (triángulos y componentes), parámetros que hubo que cambiar y observaciones del color por vértice con RGB real.
