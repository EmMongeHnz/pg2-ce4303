# Plan por milestones — AstreaView (Proyecto Grupal 2)

Derivado de las fases de `implementation-plan.md`. Los criterios de aceptación salen de su sección **Verificación**.

> **Pendiente:** fecha de entrega y tamaño del equipo. Cuando se confirmen, se agregan fechas a cada milestone y se asignan los frentes a personas. Hasta entonces el plan solo fija **orden, dependencias y gates**.

Reglas de trabajo:
- Cada milestone cierra con un **gate** (demo o verificación). Lo que depende de un milestone no se da por terminado sin su gate.
- **Los riesgos primero:** nada que dependa de un spike de M0 se construye antes de que ese spike pase o tenga un respaldo decidido.
- El protocolo (framing y mensajes) se **congela al cerrar M2**. Cambios posteriores requieren acuerdo del equipo y actualizar `docs/block-diagram.dot` (etiquetas N1–N4).
- Antes de integrar, cada componente prueba contra datos simulados (capturas sintéticas, eventos de joystick falsos, `CameraState` grabado). En la integración se reemplazan por los reales.
- Código, comentarios y nombres de archivo en inglés (kebab-case); documentos en español.
- La documentación (M7) se escribe **durante** todo el proyecto, no al final: cada gate deja diagramas, mediciones y evidencias de aprendizaje.

---

## Resumen

| Milestone | Fase del plan | Área de la especificación | Depende de |
|---|---|---|---|
| **M0** Spikes de viabilidad y esqueleto | 0 | Todas (riesgos) | — |
| **M1** Driver `holo-io` | 1 | I/O de datos (driver) | M0 (esqueleto) |
| **M2** Red segura | 2 | Seguridad | M0 (esqueleto) |
| **M3** Tubería en un nodo | 3 | Hardware (procesamiento) | M0a, M0b |
| **M4** Distribución | 4 | Distribución | M2, M3 |
| **M5** Renderer y sincronización | 5 | Hardware (procesamiento), Distribución | M0c, M1, M2, M3 |
| **M6** Maqueta e integración fin a fin | 6 | Hardware | M0d, M4, M5 |
| **M7** Documentación IEEE | 7 | Documentación | Todos (cierra después de M6) |
| **M8** Opcionales | 8 | Hardware (movimiento), I/O | M6 |

### Grafo de dependencias

```
M0 ──┬──► M1 (driver) ──────────────────┐
     ├──► M2 (red segura) ──┬──► M4 ────┤
     └──► M3 (tubería) ─────┴──► M5 ────┴──► M6 (fin a fin) ──► M8 (opcionales)
                                                   │
M7 (documentación) ── continuo ────────────────────┴──► cierre
```

**Frentes en paralelo** (para repartir cuando se conozca el equipo):
- **Frente Pi / kernel:** M0c, M0d, M1, modo `hologram` de M5, M6 (maqueta), M8.
- **Frente red:** M2, protocolo y scheduling de M4, `device-agent`.
- **Frente reconstrucción:** M0a, M0b, M3, merge de M4.
- **Frente visor:** modo `viewer` de M5, indicador de cobertura.

Después de M0, M1, M2 y M3 avanzan en paralelo sin bloquearse entre sí.

---

## M0 — Spikes de viabilidad y esqueleto

**Objetivo:** confirmar o descartar las cuatro apuestas técnicas antes de construir encima, y dejar el repositorio listo para trabajar en paralelo.

Tareas:
- [x] Estructura del repo según el plan (`driver/holo-io/`, `device-agent/`, `host/capture-service/`, `host/coordinator/`, `worker/`, `renderer/`, `common/`, `pki/`, `hardware/`, `tests/`, `docs/`) y build base de cada parte. Compilado en la PC; falta compilar en worker B y en la Pi.
- [x] **0a** Record3D → RGB-D en Python sobre Linux (`record3d` + `usbmuxd`, streaming USB comprado). Respaldo: parsear un `.r3d` exportado. _Aprobado por USB (≈27 fps, 480×640 con intrínsecas); el respaldo `.r3d` no hizo falta y su lector sigue sin probarse con un archivo real._
- [x] **0b** Pose con el anillo ArUco sobre la base manual, usando el RGB de Record3D (`cv2.aruco` + `solvePnP`). Incluye el generador del anillo en `hardware/`. _Aprobado con el anillo real: estática, pasos de 30° y oclusión._
- [ ] **0c** raylib con `PLATFORM_DRM` en la Pi 3 (Raspberry Pi OS Lite 64 bits) renderizando un GLB de ~8k triángulos. _Renderer y builds DRM/GLES2 verificados en PC; falta medir fps en la Pi 3._
- [ ] **0d** Prueba de reflejo con el monitor boca arriba y el panel a 45°. _Modo `hologram` listo; falta la prueba óptica._

Entregables: scripts de los spikes en `tests/spikes/` (o rama aparte), nota corta con resultado y decisión de cada spike.

**Gate M0:**
- 0a: se obtienen frames RGB-D con intrínsecas en Python (por USB o, si falla, desde `.r3d`; la decisión queda escrita).
- 0b: la pose del plato se estima de forma estable con parte de los marcadores tapados.
- 0c: ≥30 fps con 8k triángulos en la Pi 3 (si no se alcanza: bajar a 720p o reducir LOD-low, y documentarlo).
- 0d: el reflejo es visible con la iluminación prevista para la demo.
- El repo compila/arranca en esqueleto en las tres máquinas.

---

## M1 — Driver `holo-io` (`/dev/holo-input` con joystick)

**Objetivo:** módulo de kernel propio que expone el joystick GPIO como un char device con interfaz extensible.

Tareas:
- [ ] Overlay de device tree: 4 direcciones + 4 botones con `bias-pull-up`; nodos de ZX (I2C1 `0x10`, pin DR) y stepper (4 GPIO) declarados con `status = "disabled"`.
- [ ] `struct HoloEvent{source, code, value, timestampNs}` en un header compartido con el `device-agent`; `source = JOYSTICK`.
- [ ] IRQ por ambos flancos en cada GPIO (API `gpiod_*`).
- [ ] Debounce con `hrtimer` (~5–10 ms) antes de leer el estado estable.
- [ ] Autorepeat con `hrtimer` periódico mientras una dirección se mantiene.
- [ ] Cola `kfifo` + `wait_queue`; `read()` y `poll()`.
- [ ] `ioctl` para debounce y tasa de repetición.
- [ ] Estadísticas en `sysfs` (IRQs, rebotes filtrados, eventos).
- [ ] Carga automática al arrancar.
- [ ] Lector de prueba en espacio de usuario que imprime eventos.

Entregables: `driver/holo-io/` (`holo-io.c`, `holo-joystick.c`, `holo-io-overlay.dts`, `Makefile`), lector de prueba, pruebas en `tests/`.

**Gate M1:**
- `insmod` y `dmesg` limpios.
- El lector de prueba imprime los eventos del joystick.
- 100 pulsaciones de un botón producen exactamente 100 eventos, y las estadísticas de `sysfs` coinciden.
- `rmmod` sin fugas de IRQ ni de GPIO.

---

## M2 — Red segura

**Objetivo:** todo el tráfico entre los 3 nodos va por TCP con TLS 1.3 y autenticación mutua.

Tareas:
- [ ] `pki/make-certs.sh` (OpenSSL): CA propia y un certificado por nodo (host, worker B, Pi).
- [ ] Contexto TLS en `common/`: TLS 1.3, mTLS obligatorio, ChaCha20-Poly1305 preferido, ECDHE.
- [ ] Framing `[u32 length][u8 type][payload msgpack]` con pruebas de tramas parciales y corruptas.
- [ ] Mensajes: `HELLO`, `JOB_ASSIGN`, `JOB_RESULT`, `HEARTBEAT`, `INPUT_EVENT`, `CAMERA_STATE`, `MODEL_PUSH` (chunks + SHA-256); `ROTATE` / `ROTATE_DONE` definidos pero sin usar.
- [ ] `config.toml` con la lista estática de nodos.
- [ ] Conexión y heartbeat entre los 3 nodos reales.
- [ ] Especificación del protocolo en `docs/` coherente con N1–N4 del diagrama de bloques.
- [ ] Certificados de ejemplo para la entrega: `.gitignore` excluye `pki/**/*.key` y `pki/**/*.pem`, así que los de ejemplo (CA y nodos con claves de prueba, nunca las de la demo) se agregan con `git add -f` o con una excepción `!` en `.gitignore`. Se incluyen en el paquete final junto con `config.toml` y el overlay (ver M7).

Entregables: `common/` (protocolo, TLS, config), `pki/`, pruebas de protocolo, especificación escrita.

**Gate M2:**
- Los 3 nodos se conectan e intercambian `HEARTBEAT`.
- Wireshark muestra solo TLS 1.3.
- Se rechaza un cliente sin certificado y uno con certificado de otra CA.
- Protocolo congelado.

---

## M3 — Tubería de reconstrucción en un nodo

**Objetivo:** de vistas RGB-D con pose a un GLB limpio con dos LOD, primero en datos sintéticos y luego en capturas reales.

Tareas:
- [x] `capture-service`: ingesta Record3D (o `.r3d`), tracking ArUco, keyframe cada ≥10° con pose estable ~0.5 s, promedio de 3–5 frames de profundidad. _`astrea-capture` (`capture_service.capture`, `keyframe_selector`, `depth_averager`, `keyframe_recorder`, `coverage`) guarda cada keyframe como `ViewPackage` en `out/scans/<nombre>/`. Probado con poses sintéticas; falta validarlo con el iPhone y el plato (`docs/m3/real-capture-guide.md`)._
- [x] Prueba sintética: renderizar profundidad de una malla conocida (p. ej. Stanford bunny) desde poses conocidas. _`tests/spikes/m3-synthetic/` y `capture_service.synthetic_depth`; ver `docs/m3/synthetic-test.md`._
- [x] TSDF propia en NumPy: cubo de 25 cm a 192³, canales `tsdf`, `weight`, `color`. _`astrea_common.tsdf`, integración por franjas, peso sin tope y `MergeVolumes` exacto (listo para M4)._
- [x] Preprocesado por vista: recorte por profundidad (20–50 cm), plano del plato por RANSAC, recorte cilíndrico, outliers. _`astrea_common.view_preprocess`: rango de profundidad, recorte en el marco del plato con la pose ArUco (en lugar de RANSAC: el plano ya es conocido y el paso es determinista), píxeles sin soporte y componentes pequeñas. Solo usa datos de una vista, así que repartir vistas entre workers no cambia el resultado (`tests/unit/test_distributed_equivalence.py`). Los parámetros por defecto no están ajustados con capturas reales._
- [x] Post-proceso: marching cubes, componente conexa mayor, limpieza de duplicados y degenerados, Taubin leve, decimación cuádrica (LOD-high ≈ 30k, LOD-low ≈ 8k), color por vértice. _`coordinator.meshing` y `coordinator.postprocess`. En el bunny sintético los dos LOD salen con una sola componente y el mismo error de exactitud que la malla sin decimar. El color por vértice se prueba con un volumen sintético; falta verlo con RGB real._
- [x] Exportación GLB y compresión zstd. _`Lod.ToGlb` / `ToCompressedGlb` y `astrea_common.compression`. Bunny sintético: LOD-high 594 KiB (485 KiB con zstd), LOD-low 160 KiB (131 KiB). Falta comprobar que el renderer carga el GLB generado._
- [x] Fidelidad máxima por software (sin límite de tiempo): muestreo bilineal de profundidad y color, pesos por vista (ángulo de incidencia y profundidad), color en float, rejilla ajustada al objeto con vóxel de 0.65 mm, `lod-full.glb` sin tope de triángulos (high/low conservan 30k/8k para la Pi), promedio de todos los frames y poses de la ventana estable, y refinamiento opcional de pose (`--refine-poses turntable|icp`). _Medido solo con datos sintéticos (`docs/m3/synthetic-test.md`); el ruido real de la TrueDepth no está medido. El renderer gana `--up-axis z` y `--rotate-z` para ver estos GLB._
- [x] Medición de Chamfer contra la malla original. _`coordinator.mesh_metrics`: exactitud y completitud por separado._

Entregables: `host/capture-service/`, núcleo de fusión reutilizable por `worker/`, post-proceso en `host/coordinator/`, prueba sintética en `tests/`.

**Gate M3:**
- Error de ángulo ArUco < 1° contra posiciones marcadas en el plato.
- Chamfer de la prueba sintética bajo el umbral (fijar el umbral al iniciar M3). _Umbral fijado: exactitud (reconstrucción → original) con media ≤ 2 vóxeles y p95 ≤ 4 vóxeles, en la corrida sin ruido; la completitud solo se reporta, sobre las caras visibles. Resultado: media 0.16 vóxeles y p95 0.38 vóxeles, cumplido (`docs/m3/synthetic-test.md`)._
- Las mallas reales respetan el presupuesto de triángulos y tienen una sola componente.

---

## M4 — Distribución

**Objetivo:** la reconstrucción se reparte entre ≥3 workers y da el mismo resultado que en un nodo.

Tareas:
- [ ] `worker/`: recibe `JOB_ASSIGN` (vistas + poses), integra en la rejilla común y devuelve en `JOB_RESULT` solo bloques con peso > 0, comprimidos con zstd.
- [ ] `coordinator`: merge exacto `D = Σ wᵢ·dᵢ / Σ wᵢ` (igual para color).
- [ ] Medición de rendimiento por worker y reparto proporcional (la Pi 3 recibe pocas vistas).
- [ ] Timeout y reasignación del trabajo de un worker caído.
- [ ] Benchmark 1 vs 3 workers.

Entregables: `worker/`, scheduling y merge en `host/coordinator/`, pruebas de equivalencia, tabla de speedup.

**Gate M4:**
- 3 workers dan el mismo resultado que 1 (tolerancia ε).
- Speedup reportado.
- Si se mata un worker a mitad del trabajo, la reconstrucción igual termina.

---

## M5 — Renderer y sincronización de cámara

**Objetivo:** un solo binario de visualización en PC y en la Pi, con el host como fuente de verdad del estado de cámara y el joystick controlando ambos.

Tareas:
- [ ] `renderer` (C + raylib): `--mode viewer|hologram --lod high|low`, carga GLB.
- [ ] Cámara orbital (yaw, pitch, zoom) y autorrotación opcional.
- [ ] Modo `hologram`: fondo negro, espejado según orientación del panel, iluminación alta, `PLATFORM_DRM` en la Pi.
- [ ] Socket Unix entre el proceso Python local y el renderer.
- [ ] Host: combina mouse local y `INPUT_EVENT` remoto; emite `CAMERA_STATE{yaw, pitch, zoom, modelId}` al cambiar, ≤60 Hz.
- [ ] `device-agent`: lee `/dev/holo-input`, mapea el joystick (←/→ yaw continuo, ↑/↓ pitch, A/B zoom, C reset, D siguiente modelo), envía `INPUT_EVENT`, recibe `CAMERA_STATE` y `MODEL_PUSH` (LOD-low) y los pasa al renderer.
- [ ] Indicador de cobertura de 360° en el visor del host.

Entregables: `renderer/`, `device-agent/`, lógica de estado de cámara en el host.

**Gate M5:**
- El mismo modelo se ve en el visor y en la Pi, recibido cifrado una sola vez.
- El joystick rota y hace zoom en ambos sincronizados.
- Latencia joystick → holograma < 100 ms (medida y documentada).

---

## M6 — Maqueta e integración fin a fin

**Objetivo:** el sistema completo funcionando en la maqueta física, listo para la demo.

Tareas:
- [ ] Caja Pepper's Ghost negra mate, panel a 45°, monitor boca arriba.
- [ ] Montaje del joystick y botones.
- [ ] Base giratoria manual con anillo ArUco impreso, superficie mate clara.
- [ ] Prueba fin a fin repetida y guion de demo.
- [ ] Ajustes de rendimiento y robustez encontrados en la integración.

Entregables: maqueta, guion de demo, video de respaldo de la demo.

**Gate M6 (fin a fin):** girar el objeto a mano → captura automática → modelo en el visor → aparece en el holograma → el joystick rota y hace zoom en ambos sincronizados, con latencia < 100 ms.

---

## M7 — Documentación (IEEE-Trans, en español)

**Objetivo:** cubrir todas las secciones que pide `especificacion.md`. Avanza en paralelo desde M0 y cierra después de M6.

Tareas:
- [ ] Introducción (teoría y descripción).
- [ ] Ambiente de desarrollo (frameworks, bibliotecas, apps de terceros, herramientas).
- [ ] Atributos — **Aprendizaje Continuo**: necesidades de aprendizaje, tecnologías, acciones implementadas **con evidencia**, evaluación crítica de su eficiencia. Recolectar evidencias en cada gate.
- [ ] Diseño: diagramas de **arquitectura, componentes, secuencia** (escaneo y joystick → holograma) y **funcionalidades**; reutilizar `docs/block-diagram.dot`.
- [ ] Resultados de M1–M6 (estadísticas del driver, captura Wireshark, Chamfer, speedup, latencia).
- [ ] Instrucciones de uso, conclusiones, sugerencias y recomendaciones, referencias.
- [ ] README con cómo compilar, generar certificados y ejecutar cada nodo.

Entregables: paper en `docs/`, diagramas, README; junto con el código fuente y los archivos de ejecución (certificados de ejemplo, `config.toml`, overlay).

**Gate M7:** todas las secciones y los cuatro diagramas obligatorios presentes; las evidencias de aprendizaje continuo están adjuntas.

---

## M8 — Opcionales (en orden)

Solo después de cerrar M6. Las interfaces ya existen desde M1 y M2.

**8a — Tornamesa motorizada** (refuerza el área de Hardware con movimiento)
- [ ] `/dev/turntable`: `ioctl(ROTATE, steps)` con medio paso por `hrtimer` y rampa, `GET_POSITION`, `poll()` al terminar, bobinas desenergizadas al final.
- [ ] Habilitar el nodo del stepper en el overlay.
- [ ] Plato impreso en 3D sobre lazy susan, acople en D (opcional piñón/corona 1:3–1:5).
- [ ] `ROTATE` / `ROTATE_DONE` en `capture-service`; ArUco sigue compensando el backlash.
- **Gate 8a:** un escaneo completo se hace sin girar la base a mano.

**8b — ZX Gesture Sensor**
- [ ] `i2c_driver` con IRQ threaded en el pin DR que publica en el mismo `kfifo` con `source = ZX_GESTURE`.
- [ ] Mapeo de gestos en el `device-agent` sin nuevo nodo de dispositivo.
- **Gate 8b:** los gestos controlan la cámara igual que el joystick.

---

## Riesgos abiertos

- **Hardware sin 8a:** si la tornamesa no se implementa, el área de Hardware se defiende con el display Pepper's Ghost, el renderer y el joystick. Conviene decidir pronto si 8a entra en el alcance.
- **Rendimiento de la Pi 3:** depende de 0c; si es justo, el worker de la Pi se reduce al mínimo de vistas.
- **Record3D por USB en Linux:** depende de 0a; el respaldo `.r3d` cambia la captura automática por una selección de vistas posterior.
