**Plan de milestones y acciones — AstreaView (Proyecto Grupal 2)**

Derivado de las fases de `implementation-plan.md`. Los criterios de aceptación salen de su sección **Verificación**. En cada milestone, los números entre paréntesis de un criterio remiten a las tareas que lo cubren (`(1,2)` = tareas `Mn.1` y `Mn.2`).

> **Pendiente:** fecha de entrega y tamaño del equipo. Cuando se confirmen, se agregan fechas a cada milestone y se asignan los frentes a personas. Hasta entonces el plan solo fija **orden, dependencias y criterios**.

**Reglas de trabajo**

- Cada milestone cierra con sus criterios de aceptación (demo o verificación). Lo que depende de un milestone no se da por terminado sin ellos.
- **Los riesgos primero:** nada que dependa de un spike de M0 se construye antes de que ese spike pase o tenga un respaldo decidido.
- El protocolo (framing y mensajes) se **congela al cerrar M2**. Cambios posteriores requieren acuerdo del equipo y actualizar `docs/block-diagram.dot` (etiquetas N1–N4).
- Antes de integrar, cada componente prueba contra datos simulados (capturas sintéticas, eventos de botones falsos, `CameraState` grabado). En la integración se reemplazan por los reales.
- Código, comentarios y nombres de archivo en inglés (kebab-case); documentos en español.
- La documentación (M7) se escribe **durante** todo el proyecto, no al final: cada milestone deja diagramas, mediciones y evidencias de aprendizaje.
- `docs/` no se versiona por defecto: solo contiene lo que el equipo pide explícitamente y se agrega al repo bajo demanda.

**Resumen**

| Milestone | Fase del plan | Área de la especificación | Depende de |
|---|---|---|---|
| **M0** Spikes de viabilidad y esqueleto | 0 | Todas (riesgos) | — |
| **M1** Driver `holo-io` | 1 | I/O de datos (driver) | M0.1 |
| **M2** Red segura | 2 | Seguridad | M0.1 |
| **M3** Tubería en un nodo | 3 | Hardware (procesamiento) | M0.2, M0.3 |
| **M4** Distribución | 4 | Distribución | M2, M3 |
| **M5** Renderer y sincronización | 5 | Hardware (procesamiento), Distribución | M0.4, M1, M2, M3 |
| **M6** Maqueta e integración fin a fin | 6 | Hardware | M0.5, M4, M5 |
| **M7** Documentación IEEE | 7 | Documentación | Todos (cierra después de M6) |
| **M8** Opcionales | 8 | Hardware (movimiento), I/O | M6 |

**Grafo de dependencias**

```
M0 ──┬──► M1 (driver) ──────────────────┐
     ├──► M2 (red segura) ──┬──► M4 ────┤
     └──► M3 (tubería) ─────┴──► M5 ────┴──► M6 (fin a fin) ──► M8 (opcionales)
                                                   │
M7 (documentación) ── continuo ────────────────────┴──► cierre
```

**Frentes en paralelo** (para repartir cuando se conozca el equipo)

- **Frente Pi / kernel:** M0.4, M0.5, M1, modo `hologram` de M5, M6 (maqueta), M8.
- **Frente red:** M2, protocolo y scheduling de M4, `device-agent`.
- **Frente reconstrucción:** M0.2, M0.3, M3, merge de M4.
- **Frente visor:** modo `viewer` de M5, indicador de cobertura.

Después de M0, M1, M2 y M3 avanzan en paralelo sin bloquearse entre sí.

---

# Milestone 0 — Spikes de viabilidad y esqueleto

Confirmar o descartar las cuatro apuestas técnicas antes de construir encima, y dejar el repositorio listo para trabajar en paralelo. Depende de: —.

**Tareas**

- [x] M0.1: Estructura del repo según el plan (`driver/holo-io/`, `device-agent/`, `host/capture-service/`, `host/coordinator/`, `worker/`, `renderer/`, `common/`, `pki/`, `hardware/`, `tests/`, `docs/`) y build base de cada parte; Depende de: —;
- [x] M0.2: Spike 0a. Record3D → RGB-D en Python sobre Linux (`record3d` + `usbmuxd`, streaming USB comprado). Respaldo: parsear un `.r3d` exportado; Depende de: —;
- [x] M0.3: Spike 0b. Pose con el anillo ArUco sobre la base manual, usando el RGB de Record3D (`cv2.aruco` + `solvePnP`). Incluye el generador del anillo en `hardware/`; Depende de: M0.2;
- [ ] M0.4: Spike 0c. raylib con `PLATFORM_DRM` en la Pi 3 (Raspberry Pi OS Lite 64 bits) renderizando un GLB de ~8k triángulos; Depende de: M0.1;
- [ ] M0.5: Spike 0d. Prueba de reflejo con el monitor boca arriba y el panel a 45°; Depende de: M0.1;

**Criterio de aceptación/éxito**

- [x] Cumple 0a: se obtienen frames RGB-D con intrínsecas en Python (por USB o, si falla, desde `.r3d`; la decisión queda escrita) (2)
- [x] Cumple 0b: la pose del plato se estima de forma estable con parte de los marcadores tapados (3)
- [ ] Cumple 0c: ≥30 fps con 8k triángulos en la Pi 3 (si no se alcanza: bajar a 720p o reducir LOD-low, y documentarlo) (4)
- [ ] Cumple 0d: el reflejo es visible con la iluminación prevista para la demo (5)
- [ ] El repo compila/arranca en esqueleto en las tres máquinas (1)

**Notas**

- M0.1: compilado en la PC; falta compilar en worker B y en la Pi.
- M0.2: aprobado por USB (≈27 fps, 480×640 con intrínsecas); el respaldo `.r3d` no hizo falta y su lector sigue sin probarse con un archivo real.
- M0.3: aprobado con el anillo real: estática, pasos de 30° y oclusión.
- M0.4: renderer y builds DRM/GLES2 verificados en PC; falta medir fps en la Pi 3.
- M0.5: modo `hologram` listo; falta la prueba óptica.
- Entregables: scripts de los spikes en `tests/spikes/` (o rama aparte), nota corta con resultado y decisión de cada spike.

---

# Milestone 1 — Driver `holo-io` (`/dev/holo-input` con 5 botones)

Módulo de kernel propio que expone los 5 botones GPIO como un char device con interfaz extensible. Depende de: M0.1.

**Tareas**

- [ ] M1.1: Overlay de device tree: 5 botones (BCM 4, 27, 22, 23, 24) con `bias-pull-up`; nodos de ZX (I2C1 `0x10`, pin DR) y stepper (4 GPIO) declarados con `status = "disabled"`; Depende de: M0.1;
- [x] M1.2: `struct HoloEvent{source, code, value, timestampNs}` en un header compartido con el `device-agent`; `source = JOYSTICK`; Depende de: M0.1;
- [x] M1.3: IRQ por ambos flancos en cada GPIO (API `gpiod_*`); Depende de: M1.1;
- [x] M1.4: Debounce con `hrtimer` (~5–10 ms) antes de leer el estado estable; Depende de: M1.3;
- [x] M1.5: Autorepeat con `hrtimer` periódico mientras se mantienen derecha, izquierda, zoom + o zoom − (no el botón de autorrotación); Depende de: M1.4;
- [x] M1.6: Cola `kfifo` + `wait_queue`; `read()` y `poll()`; Depende de: M1.2, M1.4;
- [x] M1.7: `ioctl` para debounce y tasa de repetición; Depende de: M1.5, M1.6;
- [x] M1.8: Estadísticas en `sysfs` (IRQs, rebotes filtrados, eventos); Depende de: M1.4, M1.6;
- [x] M1.9: Carga automática al arrancar; Depende de: M1.1, M1.6;
- [x] M1.10: Lector de prueba en espacio de usuario que imprime eventos; Depende de: M1.6;

**Criterio de aceptación/éxito**

- [x] Cumple `insmod` y `dmesg` limpios (1,3,4,5,6,7,8)
- [x] Cumple que el lector de prueba imprime los eventos de los botones (6,10)
- [x] Cumple que 100 pulsaciones de un botón producen exactamente 100 eventos, y las estadísticas de `sysfs` coinciden (4,6,8,10)
- [x] Cumple `rmmod` sin fugas de IRQ ni de GPIO (3,6)

**Notas**

- Diseño de entradas: 5 botones entre un GPIO y GND (BCM 4 derecha, 27 izquierda, 22 zoom +, 23 zoom −, 24 activar/desactivar la rotación automática). No hay rotación vertical: el pitch queda fijo en 20°. Reset y modelo siguiente solo existen en el teclado del host. `source` sigue siendo `JOYSTICK` (1) para no tocar el protocolo congelado.
- M1.1 (sin tickear): `holo-io-overlay.dts` declara los 5 botones con pull-up (`brcm,pull = <2>`) y funciona en la Pi, pero los nodos de ZX y stepper siguen sin declarar porque faltan sus pines.
- M1.2: `driver/holo-io/holo-event.h` es la fuente de verdad (registro de 16 bytes: `timestampNs` u64, `source` u16, `code` u16, `value` s32; códigos 1–5 para derecha, izquierda, zoom +, zoom − y autorrotación; `value` 0 suelta, 1 pulsa, 2 repite). `astrea_common.holo_event` lo espeja y `tests/unit/test_holo_event.py` compara ambos lados y compila el header con `gcc`.
- El host corta el movimiento continuo de los botones cuando dejan de llegar repeticiones (`BUTTON_HOLD_TIMEOUT_S`, 0.3 s): M1.5 debe repetir los cuatro botones de movimiento con un periodo claramente menor, y no repetir el de autorrotación.
- Resultados en la Pi 3 (Raspberry Pi OS 64 bits, kernel `6.18.60+rpt-rpi-v8`; el módulo compila y carga con las guardas de versión; antes solo se había compilado contra los headers 6.12 de x86):
  - `dmesg`: una sola línea del módulo, `loaded 0.1.0: 5 buttons, debounce 10 ms, repeat 50 ms`; el nodo `/dev/holo-input` queda en el grupo `input` con modo 0660.
  - `check-presses.sh 100` con el botón de autorrotación: lector 100, sysfs 100, `dropped` 0. Contadores finales: `irqs=1439 bounces_filtered=1225 presses=107 releases=107 repeats=18` (107 incluye 7 pulsaciones de pruebas anteriores). El debounce de 10 ms absorbió unos 6.7 flancos por transición sin generar eventos de más.
  - `ioctl`: lectura y escritura de debounce y repetición correctas (`-D 8 -R 40`), y `-R 5` rechazado con `EINVAL`. Con un lector abierto, un segundo `open` da `EBUSY`.
  - `rmmod` con el lector cerrado: sin salida en `/proc/interrupts` ni en `/sys/kernel/debug/gpio`, y `modprobe` recarga sin errores. No se probó `rmmod` con el lector abierto.
  - Carga al arrancar: tras `sudo make install` y `dtoverlay=holo-io` bajo `[all]` en `config.txt`, el módulo y `/dev/holo-input` aparecen solos al reiniciar.
  - Las repeticiones se vieron (`repeats=18`), pero no se midió el periodo ni que no llegue un `repeat` después de un `release`.
- Mantenimiento: el módulo se instala para el kernel que corre al ejecutar `make install`; tras actualizar el kernel con `apt` hay que repetir `make clean && make && sudo make install`, o `modinfo holo-io` no lo encuentra y no se carga solo. La Pi mostró avisos `Undervoltage detected!` durante las pruebas, sin efecto visible en los resultados.
- Criterio «100 pulsaciones = 100 eventos»: se mide en la diferencia de eventos `PRESS` de un solo botón, mejor el de autorrotación porque no repite; `driver/holo-io/check-presses.sh` compara lo que ve `holo-reader` con `presses` en sysfs y exige `dropped = 0`. Las estadísticas están en `/sys/class/misc/holo-input/device/stats/`.
- Carga automática (M1.9): el overlay crea el nodo `holo-io` y el módulo se enlaza por `MODULE_DEVICE_TABLE(of)`. Se instala con `sudo make install` (módulo, regla udev y `holo-io.dtbo`) y se añade `dtoverlay=holo-io` a `/boot/firmware/config.txt`.
- Entregables: `driver/holo-io/` (`holo-core.c`, `holo-buttons.c`, `holo-io-overlay.dts`, `Makefile`), lector de prueba, pruebas en `tests/`.

---

# Milestone 2 — Red segura

Todo el tráfico entre los 3 nodos va por TCP con TLS 1.3 y autenticación mutua. Depende de: M0.1.

**Tareas**

- [x] M2.1: `pki/make-certs.sh` (OpenSSL): CA propia y un certificado por nodo (host, worker B, Pi); Depende de: M0.1;
- [x] M2.2: Contexto TLS en `common/`: TLS 1.3, mTLS obligatorio, ChaCha20-Poly1305 preferido, ECDHE; Depende de: M2.1;
- [x] M2.3: Framing `[u32 length][u8 type][payload msgpack]` con pruebas de tramas parciales y corruptas; Depende de: M0.1;
- [x] M2.4: Mensajes `HELLO`, `JOB_ASSIGN`, `JOB_RESULT`, `HEARTBEAT`, `INPUT_EVENT`, `CAMERA_STATE`, `MODEL_PUSH` (chunks + SHA-256); `ROTATE` / `ROTATE_DONE` definidos pero sin usar; Depende de: M2.3;
- [x] M2.5: `config.toml` con la lista estática de nodos; Depende de: M0.1;
- [x] M2.6: Conexión y heartbeat entre los 3 nodos reales; Depende de: M2.2, M2.4, M2.5;
- [x] M2.7: Especificación del protocolo en `docs/` coherente con N1–N4 del diagrama de bloques; Depende de: M2.4;
- [x] M2.8: Certificados de ejemplo para la entrega; Depende de: M2.1;

**Criterio de aceptación/éxito**

- [x] Cumple que los 3 nodos se conectan e intercambian `HEARTBEAT` (6)
- [ ] Cumple que Wireshark muestra solo TLS 1.3 (2,6)
- [ ] Cumple que se rechaza un cliente sin certificado y uno con certificado de otra CA (2,6)
- [ ] Cumple que el protocolo queda congelado (3,4,7)

**Notas**

- M2.1: `pki/make-certs.sh` genera una CA ECDSA P-256 y un certificado por nodo (nombre en el SAN DNS, `--ip` opcional); `pki/openssl-chacha20.cnf` deja un proceso solo con ChaCha20-Poly1305 (Python no permite elegir suites TLS 1.3, se activa con `OPENSSL_CONF`).
- M2.2: `astrea_common.tls`: TLS 1.3 fijo, mTLS obligatorio, confianza solo en la CA propia, el cliente verifica el nombre del hub y el hub lee el nombre del cliente desde el SAN.
- M2.3: `astrea_common.framing`: `length` cuenta `type` + payload, tope de 4 MiB (los emisores apuntan a ≤ 1 MiB por trama), decodificador incremental probado con cortes en cualquier posición y flujos corruptos.
- M2.4: `astrea_common.messages`: validación de campos, reglas de quién envía qué por rol, `MODEL_PUSH` en chunks con SHA-256 y bloques de arrays con zstd acotado. `JOB_ASSIGN` y `JOB_RESULT` ya traen sus campos definidos para M4. `ROTATE` / `ROTATE_DONE` existen pero el hub los rechaza.
- M2.5: `config.toml` en la raíz (nodos, roles, rutas TLS con `{node}`, parámetros de heartbeat), validado por `astrea_common.config`.
- M2.6: topología hub-and-spoke (el host escucha, worker B y la Pi se conectan) en `astrea_common.link`; `astrea-net-check serve|connect` verifica los enlaces. Validado en loopback con 3 nodos y certificados distintos, y en las máquinas reales: host (PC), laptop B (worker) y Pi 3 (worker y device-agent) conectados a la vez con `astrea-net-check`. La Pi negocia ChaCha20-Poly1305 con `OPENSSL_CONF=pki/openssl-chacha20.cnf` (pasos en `docs/protocol.md`, sección 10).
- M2.7: especificación en `docs/protocol.md` (versionada con `git add -f`), con la guía de prueba en las máquinas reales.
- M2.8: certificados de ejemplo en `pki/example/` (CA y nodos con claves de prueba, agregados con `git add -f`).
- Criterios: el rechazo de clientes sin certificado o de otra CA, TLS 1.3 y el intercambio de `HEARTBEAT` están cubiertos por `tests/unit/test_tls.py` y `tests/unit/test_secure_link.py` (incluye un proxy que comprueba que lo único en la red son registros TLS). Los 3 nodos reales ya se conectan e intercambian `HEARTBEAT`; sigue sin marcarse Wireshark (captura en el host) y el rechazo con `openssl s_client` desde una máquina real (sección 10 de `docs/protocol.md`). El protocolo queda propuesto como congelado, pendiente de acuerdo del equipo.
- M2.8: `.gitignore` excluye `pki/**/*.key` y `pki/**/*.pem`, así que los de ejemplo (CA y nodos con claves de prueba, nunca las de la demo) se agregan con `git add -f` o con una excepción `!` en `.gitignore`. Se incluyen en el paquete final junto con `config.toml` y el overlay (ver M7).
- Entregables: `common/` (protocolo, TLS, config), `pki/`, pruebas de protocolo, especificación escrita.

---

# Milestone 3 — Tubería de reconstrucción en un nodo

De vistas RGB-D con pose a un GLB limpio con dos LOD, primero en datos sintéticos y luego en capturas reales. Depende de: M0.2, M0.3.

**Tareas**

- [x] M3.1: `capture-service`: ingesta Record3D (o `.r3d`), tracking ArUco, keyframe cada ≥10° con pose estable ~0.5 s, promedio de 3–5 frames de profundidad; Depende de: M0.2, M0.3;
- [x] M3.2: Prueba sintética: renderizar profundidad de una malla conocida (p. ej. Stanford bunny) desde poses conocidas; Depende de: M0.3;
- [x] M3.3: TSDF propia en NumPy: cubo de 25 cm a 192³, canales `tsdf`, `weight`, `color`; Depende de: M0.1;
- [x] M3.4: Preprocesado por vista: recorte por profundidad (20–50 cm), plano del plato, recorte cilíndrico, outliers; Depende de: M3.1, M3.3;
- [x] M3.5: Post-proceso: marching cubes, componente conexa mayor, limpieza de duplicados y degenerados, Taubin leve, decimación cuádrica (LOD-high ≈ 30k, LOD-low ≈ 8k), color por vértice; Depende de: M3.3;
- [x] M3.6: Exportación GLB y compresión zstd; Depende de: M3.5;
- [x] M3.7: Fidelidad máxima por software (sin límite de tiempo); Depende de: M3.3, M3.4, M3.5;
- [x] M3.8: Medición de Chamfer contra la malla original; Depende de: M3.2, M3.5;

**Criterio de aceptación/éxito**

- [ ] Cumple error de ángulo ArUco < 1° contra posiciones marcadas en el plato (1)
- [x] Cumple Chamfer de la prueba sintética bajo el umbral (2,3,5,8)
- [ ] Cumple que las mallas reales respetan el presupuesto de triángulos y tienen una sola componente (1,4,5,6,7)

**Notas**

- M3.1: `astrea-capture` (`capture_service.capture`, `keyframe_selector`, `depth_averager`, `keyframe_recorder`, `coverage`) guarda cada keyframe como `ViewPackage` en `out/scans/<nombre>/`. Probado con poses sintéticas; falta validarlo con el iPhone y el plato (`docs/m3/real-capture-guide.md`).
- M3.2: `tests/spikes/m3-synthetic/` y `capture_service.synthetic_depth`; ver `docs/m3/synthetic-test.md`.
- M3.3: `astrea_common.tsdf`, integración por franjas, peso sin tope y `MergeVolumes` exacto (listo para M4).
- M3.4: `astrea_common.view_preprocess`: rango de profundidad, recorte en el marco del plato con la pose ArUco (en lugar de RANSAC: el plano ya es conocido y el paso es determinista), píxeles sin soporte y componentes pequeñas. Solo usa datos de una vista, así que repartir vistas entre workers no cambia el resultado (`tests/unit/test_distributed_equivalence.py`). Los parámetros por defecto no están ajustados con capturas reales.
- M3.5: `coordinator.meshing` y `coordinator.postprocess`. En el bunny sintético los dos LOD salen con una sola componente y el mismo error de exactitud que la malla sin decimar. El color por vértice se prueba con un volumen sintético; falta verlo con RGB real.
- M3.6: `Lod.ToGlb` / `ToCompressedGlb` y `astrea_common.compression`. Bunny sintético: LOD-high 594 KiB (485 KiB con zstd), LOD-low 160 KiB (131 KiB). Falta comprobar que el renderer carga el GLB generado.
- M3.7: muestreo bilineal de profundidad y color, pesos por vista (ángulo de incidencia y profundidad), color en float, rejilla ajustada al objeto con vóxel de 0.65 mm, `lod-full.glb` sin tope de triángulos (high/low conservan 30k/8k para la Pi), promedio de todos los frames y poses de la ventana estable, y refinamiento opcional de pose (`--refine-poses turntable|azimuth|icp`). Medido solo con datos sintéticos (`docs/m3/synthetic-test.md`); el ruido real de la TrueDepth no está medido. El renderer gana `--up-axis z` y `--rotate-z` para ver estos GLB.
- M3.8: `coordinator.mesh_metrics`: exactitud y completitud por separado.
- Umbral de Chamfer fijado: exactitud (reconstrucción → original) con media ≤ 2 vóxeles y p95 ≤ 4 vóxeles, en la corrida sin ruido; la completitud solo se reporta, sobre las caras visibles. Resultado: media 0.16 vóxeles y p95 0.38 vóxeles, cumplido (`docs/m3/synthetic-test.md`).
- Entregables: `host/capture-service/`, núcleo de fusión reutilizable por `worker/`, post-proceso en `host/coordinator/`, prueba sintética en `tests/`.

---

# Milestone 4 — Distribución

La reconstrucción se reparte entre ≥3 workers y da el mismo resultado que en un nodo. Depende de: M2, M3.

**Tareas**

- [x] M4.1: `worker/`: recibe `JOB_ASSIGN` (vistas + poses), integra en la rejilla común y devuelve en `JOB_RESULT` solo bloques con peso > 0, comprimidos con zstd; Depende de: M2.4, M3.3, M3.4;
- [x] M4.2: `coordinator`: merge exacto `D = Σ wᵢ·dᵢ / Σ wᵢ` (igual para color); Depende de: M3.3, M4.1;
- [ ] M4.3: Medición de rendimiento por worker y reparto proporcional (la Pi 3 recibe pocas vistas); Depende de: M4.1;
- [ ] M4.4: Timeout y reasignación del trabajo de un worker caído; Depende de: M2.6, M4.1;
- [ ] M4.5: Benchmark 1 vs 3 workers; Depende de: M4.2, M4.3;

**Criterio de aceptación/éxito**

- [ ] Cumple que 3 workers dan el mismo resultado que 1 (tolerancia ε) (1,2)
- [ ] Cumple que el speedup queda reportado (5)
- [ ] Cumple que si se mata un worker a mitad del trabajo, la reconstrucción igual termina (4)

**Notas**

- M4.1: `astrea-worker --name <nodo>` (`worker.service`): recibe `JOB_ASSIGN` por partes, preprocesa e integra en la rejilla común en un hilo (los latidos siguen vivos), y devuelve solo las teselas de 32³ vóxeles con peso > 0 comprimidas con zstd. Rechaza con `error` un trabajo que no cabe en `--max-grid-mib` (por defecto, un tercio de la memoria disponible: medido, un worker llega a ~2.2 veces el tamaño del volumen). Codec en `astrea_common.job_codec` y `volume_blocks`.
- M4.2: `astrea-coordinator reconstruct <sesiones> --min-workers N` (`coordinator.distributed`): reparte las vistas en round robin sobre la rejilla que fija el coordinador y fusiona los bloques con el promedio ponderado exacto. Hasta M4.3 el reparto es uniforme y un worker caído o con error aborta la corrida.
- Tamaños medidos con el scan `cup` (59 vistas, loopback, 3 workers): con la rejilla por defecto de 0.65 mm es de 332×334×256 (28.4 M de vóxeles, 568 MiB por volumen); cada worker llega a ~1.3 GB de RSS, recibe ~18 MiB de vistas y devuelve ~142 MiB comprimidos; fusión ~100 s por worker (20 vistas) y merge 3.9 s, 110 s en total. Una Pi 3 no puede con esa rejilla (volumen de 568 MiB y pico de ~1.3 GB): con `--voxel-mm 1.3` el mismo scan pide 3.7 M de vóxeles (74 MiB). Hasta M4.4 (exclusión de un worker que falla) la Pi solo entra en corridas con vóxel grueso.
- Criterio de equivalencia: validado en loopback con 3 workers (`tests/unit/test_distributed_reconstruction.py`: volumen igual al de un nodo con ε = 1e-4) y con el scan `cup` real a 1.3 mm (59 vistas, 3 procesos en una máquina: 54967 caras en `lod-full` en ambos casos, `lod-high` 29620 frente a 29619). Corrida en máquinas reales con host y laptop B a 0.65 mm confirmada por el equipo (tiempos por anotar); falta la corrida con la Pi (a 1.3 mm) y la comparación de tiempos contra un nodo.
- Entregables: `worker/`, scheduling y merge en `host/coordinator/`, pruebas de equivalencia, tabla de speedup.

---

# Milestone 5 — Renderer y sincronización de cámara

Un solo binario de visualización en PC y en la Pi, con el host como fuente de verdad del estado de cámara y los botones del dispositivo controlando ambos. Depende de: M0.4, M1, M2, M3.

**Tareas**

- [ ] M5.1: `renderer` (C + raylib): `--mode viewer|hologram --lod high|low`, carga GLB; Depende de: M0.1, M3.6;
- [ ] M5.2: Cámara orbital (yaw, pitch, zoom) y autorrotación opcional; Depende de: M5.1;
- [ ] M5.3: Modo `hologram`: fondo negro, espejado según orientación del panel, iluminación alta, `PLATFORM_DRM` en la Pi; Depende de: M0.4, M0.5, M5.1;
- [x] M5.4: Socket Unix entre el proceso Python local y el renderer; Depende de: M5.1;
- [x] M5.5: Host: combina mouse local e `INPUT_EVENT` remoto; emite `CAMERA_STATE{yaw, pitch, zoom, modelId}` al cambiar, ≤60 Hz; Depende de: M2.4, M5.2, M5.4;
- [x] M5.6: `device-agent`: lee `/dev/holo-input`, reenvía los eventos de los botones como `INPUT_EVENT` (el host los mapea a derecha/izquierda yaw continuo, zoom +/− y autorrotación), recibe `CAMERA_STATE` y `MODEL_PUSH` (LOD-low) y los pasa al renderer; Depende de: M1.2, M1.6, M2.2, M2.4, M5.4;
- [ ] M5.7: Indicador de cobertura de 360° en el visor del host; Depende de: M3.1, M5.1;

**Criterio de aceptación/éxito**

- [x] Cumple que el mismo modelo se ve en el visor y en la Pi, recibido cifrado una sola vez (1,3,5,6)
- [ ] Cumple que los botones rotan y hacen zoom en ambos sincronizados (2,5,6)
- [ ] Cumple latencia botón → holograma < 100 ms, medida y documentada (5,6)

**Notas**

- Diseño de entradas: A/D giran, W/S hacen zoom, `R` activa o desactiva la rotación automática (20°/s, empieza desactivada) y `N` pasa al siguiente modelo; el arrastre del mouse gira solo en horizontal y no hay rueda, rotación vertical ni reset. Los botones GPIO repiten esas mismas acciones (ver M1). El renderer ya usa este mapeo (A/D, W/S, `R`, `N`, arrastre horizontal), sin rueda ni pitch.
- Dueño del hub del dispositivo: un servicio nuevo y permanente del host, `astrea-host` (`coordinator.host_service`), mantiene el estado de cámara, el `MODEL_PUSH` y el enlace con el `device-agent` en su propio puerto (`hub.device-port`, por defecto `hub.port + 1`), de modo que corre junto a `astrea-coordinator reconstruct` (hub de los workers en `hub.port`) sin pelear por el puerto. `config.toml` sigue siendo uno solo e idéntico en todas las máquinas, sin config propio para la Pi.
- M5.5 (avance): `coordinator.camera_control.CameraControl` integra teclado, mouse y botones en un único estado, sumando las fuentes y la rotación automática, y limita la emisión de `CameraState` a 60 Hz. Es lógica pura con reloj inyectado (`tests/unit/test_camera_control.py`). Ya está conectada al socket del renderer (M5.4); falta conectarla al hub y al `device-agent`.
- M5.4: socket Unix con registros binarios fijos entre `RendererBridge` (Python) y `renderer --control-socket` (C, `control-link.c`). `tests/unit/test_render_link.py` compara el header con el espejo de Python y hace hablar a `control-link.c` con un socket real; `tests/unit/test_renderer_bridge.py` prueba el puente con un renderer simulado. En la PC, el renderer real se conectó, recibió el estado (la autorrotación llegó a 60° en 3 s) y cambió de modelo con `NEXT_MODEL`. El equipo probó a mano en la PC las teclas A/D, W/S, `R`, `N` y el arrastre, y el ciclo de `N` vuelve al primer modelo tras corregir que el renderer recargue el modelo inicial. Falta el lado de la Pi, que depende del `device-agent` (M5.6). Herramienta de prueba: `uv run python -m coordinator.renderer_bridge --socket viewer.sock --models cup,bunny`.
- M5.6 (avance, sin tickear): `astrea-device-agent` (`device_agent.service`) reenvía `/dev/holo-input` al host, entrega `CAMERA_STATE` al renderer de la Pi por su socket Unix e instala los `MODEL_PUSH`. `tests/unit/test_device_agent.py` lo prueba contra un hub real con mTLS en loopback, con un FIFO en lugar del dispositivo y un renderer simulado. Falta ejecutarlo en la Pi con el driver y el renderer reales.
- M5.5 / M5.6 / `astrea-host`: `tests/unit/test_host_service.py` ejecuta `astrea-host` y el `device-agent` reales sobre mTLS en loopback, con un FIFO en lugar del driver y un visor simulado en cada lado: un botón mantenido gira los dos visores al mismo yaw, el teclado del visor del host llega a la Pi, `N` cambia el modelo en ambos, los modelos se instalan en la Pi byte a byte y, si cae la Pi con un botón pulsado, el movimiento se detiene. Las ids de modelo se leen al arrancar `astrea-host` (un modelo nuevo exige reiniciarlo), y el visor del host puede usar su propia carpeta de modelos con las mismas ids (por ejemplo la de LOD alto), porque a la Pi solo llega la carpeta de `astrea-host`. Probado por el equipo en las máquinas reales (host y Pi 3 con el driver y el renderer `hologram`, por el puerto 7401): los modelos se envían y se instalan en la Pi, y los botones giran y hacen zoom en el monitor de la Pi. Falta confirmar la sincronía con el visor del host al mismo tiempo y medir la latencia botón → holograma (criterios sin tickear). Observado: cada cierto tiempo el movimiento de la Pi se congela unos 0.8 s; la Pi y el host estaban por Wi-Fi (hotspot) y no por el cable directo, y la Pi mostró avisos de subvoltaje, así que se sospecha de la red o de la alimentación. Si el cable no lo resuelve, el renderer de la Pi puede interpolar entre estados.
- Entregables: `renderer/`, `device-agent/`, lógica de estado de cámara en el host.

---

# Milestone 6 — Maqueta e integración fin a fin

El sistema completo funcionando en la maqueta física, listo para la demo. Depende de: M0.5, M4, M5.

**Tareas**

- [ ] M6.1: Caja Pepper's Ghost negra mate, panel a 45°, monitor boca arriba; Depende de: M0.5;
- [ ] M6.2: Montaje de los 5 botones; Depende de: M1;
- [ ] M6.3: Base giratoria manual con anillo ArUco impreso, superficie mate clara; Depende de: M0.3;
- [ ] M6.4: Prueba fin a fin repetida y guion de demo; Depende de: M4, M5, M6.1, M6.2, M6.3;
- [ ] M6.5: Ajustes de rendimiento y robustez encontrados en la integración; Depende de: M6.4;

**Criterio de aceptación/éxito**

- [ ] Cumple fin a fin: girar el objeto a mano → captura automática → modelo en el visor → aparece en el holograma → los botones rotan y hacen zoom en ambos sincronizados, con latencia < 100 ms (1,2,3,4,5)

**Notas**

- Entregables: maqueta, guion de demo, video de respaldo de la demo.

---

# Milestone 7 — Documentación (IEEE-Trans, en español)

Cubrir todas las secciones que pide `especificacion.md`. Avanza en paralelo desde M0 y cierra después de M6. Depende de: todos.

**Tareas**

- [ ] M7.1: Introducción (teoría y descripción); Depende de: —;
- [ ] M7.2: Ambiente de desarrollo (frameworks, bibliotecas, apps de terceros, herramientas); Depende de: —;
- [ ] M7.3: Atributos — **Aprendizaje Continuo**: necesidades de aprendizaje, tecnologías, acciones implementadas **con evidencia**, evaluación crítica de su eficiencia. Recolectar evidencias en cada milestone; Depende de: —;
- [ ] M7.4: Diseño: diagramas de **arquitectura, componentes, secuencia** (escaneo y botones → holograma) y **funcionalidades**; reutilizar `docs/block-diagram.dot`; Depende de: M2;
- [ ] M7.5: Resultados de M1–M6 (estadísticas del driver, captura Wireshark, Chamfer, speedup, latencia); Depende de: M6;
- [ ] M7.6: Instrucciones de uso, conclusiones, sugerencias y recomendaciones, referencias; Depende de: M7.5;
- [ ] M7.7: README con cómo compilar, generar certificados y ejecutar cada nodo; Depende de: M6;

**Criterio de aceptación/éxito**

- [ ] Cumple que todas las secciones y los cuatro diagramas obligatorios están presentes (1,2,3,4,5,6)
- [ ] Cumple que las evidencias de aprendizaje continuo están adjuntas (3)

**Notas**

- Entregables: paper en `docs/`, diagramas, README; junto con el código fuente y los archivos de ejecución (certificados de ejemplo, `config.toml`, overlay).

---

# Milestone 8 — Opcionales (en orden)

Solo después de cerrar M6. Las interfaces ya existen desde M1 y M2. 8a (tornamesa motorizada) refuerza el área de Hardware con movimiento; 8b es el ZX Gesture Sensor. Depende de: M6.

**Tareas**

- [ ] M8.1: 8a. `/dev/turntable`: `ioctl(ROTATE, steps)` con medio paso por `hrtimer` y rampa, `GET_POSITION`, `poll()` al terminar, bobinas desenergizadas al final; Depende de: M1.6, M6;
- [ ] M8.2: 8a. Habilitar el nodo del stepper en el overlay; Depende de: M1.1;
- [ ] M8.3: 8a. Plato impreso en 3D sobre lazy susan, acople en D (opcional piñón/corona 1:3–1:5); Depende de: M6;
- [ ] M8.4: 8a. `ROTATE` / `ROTATE_DONE` en `capture-service`; ArUco sigue compensando el backlash; Depende de: M2.4, M8.1;
- [ ] M8.5: 8b. `i2c_driver` con IRQ threaded en el pin DR que publica en el mismo `kfifo` con `source = ZX_GESTURE`; Depende de: M1.6, M6;
- [ ] M8.6: 8b. Mapeo de gestos en el `device-agent` sin nuevo nodo de dispositivo; Depende de: M5.6, M8.5;

**Criterio de aceptación/éxito**

- [ ] Cumple 8a: un escaneo completo se hace sin girar la base a mano (1,2,3,4)
- [ ] Cumple 8b: los gestos controlan la cámara igual que los botones (5,6)

**Notas**

- Los IDs M8.1–M8.4 pertenecen a 8a y M8.5–M8.6 a 8b; 8b no depende de 8a.

---

**Riesgos abiertos**

- **Hardware sin 8a:** si la tornamesa no se implementa, el área de Hardware se defiende con el display Pepper's Ghost, el renderer y los botones. Conviene decidir pronto si 8a entra en el alcance.
- **Rendimiento de la Pi 3:** depende de M0.4; si es justo, el worker de la Pi se reduce al mínimo de vistas.
- **Record3D por USB en Linux:** depende de M0.2; el respaldo `.r3d` cambia la captura automática por una selección de vistas posterior.
