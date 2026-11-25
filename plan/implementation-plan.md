# Propuesta: AstreaView — escaneo 3D distribuido con visualización holográfica

## Contexto

Proyecto Grupal 2 de Principios de SO (`especificacion.md`). Hay que cubrir cuatro áreas:

1. **Driver propio:** módulo de kernel, de preferencia con GPIO.
2. **Hardware:** con movimiento o con procesamiento interesante.
3. **Procesamiento distribuido:** ≥3 nodos, sin compartir directorios.
4. **Cifrado** de todo el tráfico de red.

También se entrega documentación IEEE-Trans con diagramas de arquitectura, componentes, secuencia y funcionalidades.

**Idea base:** escanear un objeto con el TrueDepth del iPhone, verlo en la PC y proyectarlo en un Pepper's Ghost con control físico.

**Restricciones confirmadas**
- **No hay Mac**, por lo que no se puede hacer una app iOS nativa.
- **Raspberry Pi 3** disponible.
- Presupuesto bajo (<$30) para pantalla y maqueta.

**Decisiones del equipo**
- **Captura:** Record3D más una tubería de reconstrucción propia.
- **Base giratoria manual** (sin motor). La pose de cada vista se obtiene **solo con ArUco**.
- **Tornamesa motorizada:** queda como **fase opcional** (28BYJ-48 + plato impreso en 3D sobre rodamiento). Su interfaz se define desde el inicio.
- **Control:** **5 pulsadores por GPIO** (derecha, izquierda, zoom +, zoom −, activar o desactivar la rotación automática). No hay rotación vertical: el pitch queda fijo. El ZX Gesture Sensor queda como **fase opcional final**.
- **Holograma:** un solo panel a 45° sobre un monitor viejo.

---

## Evaluación crítica

| Problema | Por qué | Solución adoptada |
|---|---|---|
| App iOS propia sin Mac | Xcode es obligatorio. Compilar en CI macOS y hacer sideload da certificados de 7 días y no hay depurador. | Record3D transmite el RGB-D del TrueDepth. El trabajo propio está en la tubería. |
| Pose de cada vista sin motor | Si la base se gira a mano, no se conoce el ángulo. Registrar las vistas con ICP es frágil con el ruido del TrueDepth. | Un **anillo de marcadores ArUco** en el borde del plato da la pose en cada frame, aunque el objeto tape algunos marcadores. Se captura automáticamente cada ~10°. |
| Área de "Hardware" sin motor | La especificación pide "movimiento o procesamiento interesante". | El holograma y el renderer cubren la parte de procesamiento. La tornamesa motorizada queda lista para agregarse y reforzar el área. **Riesgo:** si no se implementa, el argumento de hardware se apoya en el display y los botones. |
| Enviar la visualización como video | Agrega latencia y carga en la Pi 3. | Se sincroniza solo el **estado de cámara**. La Pi renderiza localmente el modelo, que recibe cifrado una sola vez. |
| Distribución débil | El teléfono no procesa nada propio. | La reconstrucción se **paraleliza por vistas** en ≥3 workers y se combina de forma exacta. |
| Tamaño variable del modelo | Una malla sin control puede llegar a cientos de miles de vértices con ruido. | Una rejilla de vóxeles fija, limpieza, decimación a un presupuesto de triángulos y dos niveles de detalle (LOD). |

---

## Arquitectura

```
 iPhone (Record3D, TrueDepth) ──USB/Wi-Fi──► HOST PC
                                           ├─ capture-service  (tracking ArUco, captura automática)
                                           ├─ astrea-host      (servicio permanente: hub del dispositivo en hub.device-port, estado de cámara, MODEL_PUSH)
                                           ├─ coordinator      (reparte trabajos, fusiona, malla)
                                           ├─ worker
                                           └─ renderer --mode viewer
                     mTLS (TCP)                 ▲        ▲
        ┌──────────────────────────────────────┘        └───────────┐
   WORKER B (laptop del equipo)                         RASPBERRY PI 3 (dispositivo)
   └─ worker                                            ├─ módulo de kernel holo-io
                                                        │   ├─ /dev/holo-input  (5 botones GPIO; ZX después)
                                                        │   └─ /dev/turntable   (opcional)
                                                        ├─ device-agent (Python, mTLS)
                                                        ├─ worker (ligero)
                                                        └─ renderer --mode hologram → HDMI → monitor bajo panel 45°
```

**Nodos de procesamiento (≥3):**
1. **Host:** coordinación, fusión, mallado y visor.
2. **Worker B:** fusión parcial.
3. **Pi 3:** driver, render del holograma y fusión parcial ligera.

Se necesita un segundo computador del equipo que haga de worker B.

---

## Componente 1 — Captura y reconstrucción

### Captura (`host/capture-service`)
- **Fuente de datos:** la librería Python `record3d` por USB. En Linux necesita `usbmuxd` y la compra in-app del streaming USB (~$5).
- **Fallback:** grabar en la app, exportar el `.r3d` y parsearlo. Con la base manual el flujo es igual de válido, porque las vistas se eligen después.

**Anillo ArUco**
- 8–12 marcadores con IDs en ángulos conocidos, alrededor del borde del plato.
- Con `cv2.aruco` + `solvePnP` sobre todos los marcadores visibles se obtiene la pose cámara→plato.
- Así el eje de giro queda implícito y no se necesita un ángulo mecánico.

**Captura automática**
- Se toma un keyframe cuando el ángulo avanzó **≥10°** desde el anterior **y** la pose está estable unos ~0.5 s. Esa espera evita frames con la mano encima o con desenfoque.
- En cada keyframe se promedian 3–5 frames de profundidad.
- **Indicador de cobertura:** un anillo de 360° en el visor del host le muestra al usuario qué sectores faltan.
- **Vistas:** N ≈ 36. Opcionalmente, una segunda pasada con el teléfono más alto para cubrir la parte superior.

### Paralelización (`worker`)
**TSDF propia en NumPy** (volumen de distancias con signo truncadas) sobre una rejilla densa común: un cubo de 25 cm a 192³ (~1.3 mm/vóxel, ~80 MB).

Cada worker recibe un subconjunto de vistas con sus poses y hace:
1. Recorte por profundidad (20–50 cm).
2. Eliminación del plano del plato (RANSAC) y recorte cilíndrico.
3. Eliminación de outliers.
4. Integración en la TSDF (`tsdf`, `weight`, `color`).

Devuelve solo los bloques con peso > 0, comprimidos con zstd.

**Merge exacto:** el coordinador combina con `D = Σ wᵢ·dᵢ / Σ wᵢ` (igual para el color). Da el mismo resultado que una fusión en un solo nodo.

**Planificación**
- Las vistas se reparten de forma proporcional al rendimiento medido de cada worker. La Pi 3 recibe pocas.
- Si un worker no responde en un tiempo límite, su trabajo se reasigna.

### Post-proceso (`host/coordinator`)
1. Marching cubes.
2. Se conserva la componente conexa más grande.
3. Eliminación de duplicados y triángulos degenerados.
4. Suavizado Taubin leve.
5. Decimación cuádrica a un presupuesto fijo:
   - **LOD-high ≈ 30k triángulos** (PC);
   - **LOD-low ≈ 8k triángulos** (Pi 3).
6. Color por vértice, sin texturas.
7. Exportación a GLB, comprimido con zstd para la red.

**Prueba sintética:** se renderizan mapas de profundidad de una malla conocida (p. ej. el Stanford bunny) desde poses conocidas, se pasan por la tubería y se mide la distancia de Chamfer contra la original.

---

## Componente 2 — Visor (`renderer`, C + raylib)

- **Un solo binario:** `renderer --mode viewer|hologram --lod high|low`. Carga GLB. En la Pi 3 usa `PLATFORM_DRM` (GLES2 directo a KMS, sin X).
- **Cámara orbital:** yaw y zoom, con pitch fijo (20°), más autorrotación que empieza desactivada.
- **Modo hologram:** fondo negro, imagen espejada según la orientación del panel e iluminación alta.
- **El host es la fuente de verdad del estado de cámara.**
  - Un servicio permanente del host (`astrea-host`) es dueño del estado de cámara y atiende al `device-agent` en su propio puerto (`hub.device-port`, por defecto `hub.port + 1`), así que corre en paralelo con `astrea-coordinator reconstruct`, que conserva el hub de los workers en `hub.port`. Combina teclado y mouse locales con los eventos de botones remotos; las contribuciones se suman.
  - Cuando se conecta el agente, `astrea-host` le envía el estado actual, todos los modelos de su carpeta (`<id>.glb`, `MODEL_PUSH` con zstd y SHA-256) y el estado otra vez; las ids de esa carpeta son también las que recorre la tecla `N`.
  - Integra también la autorrotación (20°/s, empieza desactivada), de modo que los dos visores giran igual. El renderer del host solo reporta entradas crudas por el socket Unix y dibuja el estado que recibe.
  - Emite `CameraState{yaw, pitch, zoom, modelId}` cuando cambia, a ≤60 Hz. `pitch` viaja siempre con el valor fijo.
  - Teclado: A/D giran, W/S hacen zoom, `R` activa o desactiva la autorrotación y `N` pasa al siguiente modelo. El arrastre del mouse solo gira en horizontal (sin rueda). No hay reset.
- **Socket Unix local** entre el proceso Python y el renderer (`--control-socket`). No necesita cifrado porque no pasa por la red.
  - Son registros binarios de tamaño fijo (`ControlState` de 44 bytes hacia el renderer y `ControlInput` de 8 bytes hacia Python), definidos en `renderer/src/control-protocol.h` y espejados en `astrea_common.render_link`. Se descartó msgpack para no depender de una librería en C.
  - Conectado al socket, el renderer no gira ni mueve la cámara por su cuenta: reporta teclado y mouse y dibuja el estado recibido. El de la Pi (`hologram`) no reporta entradas.
  - `modelId` se resuelve como `<--models-dir>/<modelId>.glb`; el modelo inicial de `--model` se recarga desde su propia ruta, para que el ciclo con `N` vuelva al primero.
  - Sin socket (modo independiente) usa las mismas teclas y puede alternar su autorrotación con `R`.

---

## Componente 3 — Dispositivo holográfico (Pi 3)

### Plataforma Pi 3: suficiente, con estas notas
- **SO:** Raspberry Pi OS Lite de 64 bits, sin escritorio, para liberar RAM (1 GB).
- **Fuente:** 5 V 2.5 A.
- **Cifrado:** preferir ChaCha20-Poly1305 en TLS. La Pi 3 no tiene aceleración AES; aun así el volumen de datos es bajo.
- **Render:** 8k triángulos con GLES2 a 720p/1080p es viable. **Se valida en el spike 0c.**
- **Como worker es lenta:** recibe pocas vistas.

### Hardware y lista de materiales

| Ítem | Costo aprox. |
|---|---|
| Panel de PETG/acrílico de 1–2 mm (o el vidrio de un marco de fotos) | $3–6 |
| Monitor HDMI viejo, o panel de laptop con controladora HDMI, boca arriba | $0–20 |
| Caja negra mate por dentro (cartón o MDF) | $0–3 |
| 5 pulsadores (GPIO 4, 27, 22, 23, 24) | ~$5–10 |
| Base giratoria manual: plato de 15–20 cm, superficie mate clara, anillo ArUco impreso | ~$0–3 |
| *(Opcional)* 28BYJ-48 + ULN2003 + lazy susan + plato impreso en 3D | ~$6 |
| *(Opcional)* ZX Gesture Sensor | ~$25 |

### Driver: módulo de kernel `holo-io` (C)

**Overlay de device tree**
- Declara los 5 botones (BCM 4 derecha, 27 izquierda, 22 zoom +, 23 zoom −, 24 autorrotación) con `bias-pull-up`, cada uno entre el GPIO y GND.
- Reserva los nodos del ZX (I2C1 `0x10`, pin DR) y del stepper (4 GPIO), deshabilitados (`status = "disabled"`) hasta su fase.
- El código usa la API `gpiod_*`.

**`/dev/holo-input`** (char device; una sola interfaz para todas las entradas)
- **Formato de evento:** `struct HoloEvent{source, code, value, timestampNs}`. Hoy `source` es `JOYSTICK` (1, el valor se conserva por el protocolo; son los botones); más adelante también `ZX_GESTURE`, sin cambiar el agente. `value`: 0 suelta, 1 pulsa, 2 repite. El header `driver/holo-io/holo-event.h` es la fuente de verdad.
- **Botones:**
  - IRQ por ambos flancos en cada GPIO.
  - **Debounce** con `hrtimer` (~5–10 ms) antes de leer el estado estable.
  - **Autorepeat** de derecha, izquierda, zoom + y zoom − mientras se mantienen presionados (`hrtimer` periódico, 50 ms por defecto, la primera repetición un periodo después de la pulsación), para dar movimiento continuo. El botón de autorrotación no repite. El host detiene el movimiento si dejan de llegar repeticiones (0.3 s), por lo que el periodo máximo configurable es 100 ms.
- **Interfaz con el usuario del dispositivo:**
  - eventos encolados en un `kfifo` de 256 registros con `wait_queue`; un solo lector a la vez;
  - `read()` / `poll()`;
  - `ioctl` para configurar el debounce (1–100 ms, 10 por defecto) y la tasa de repetición (10–100 ms, 50 por defecto);
  - estadísticas en `sysfs` (IRQs, rebotes filtrados, pulsaciones, soltadas, repeticiones, descartados).
  - El contrato completo está en `docs/protocol.md`.
- **Fase opcional (ZX):** `i2c_driver` con IRQ threaded en el pin DR que publica en el mismo `kfifo`.

**`/dev/turntable`** (fase opcional)
- `ioctl(ROTATE, steps)` con medio paso por `hrtimer` y rampa de velocidad.
- `GET_POSITION`.
- `poll()` avisa cuando termina el movimiento.
- Desenergiza las bobinas al terminar.

**Mecánica de la tornamesa:**
- El rodamiento lazy susan carga el peso; el motor solo transmite el giro.
- Acople impreso con agujero en D (eje de 5 mm con caras planas a 3 mm, holgura de 0.1–0.2 mm).
- Opcional: piñón y corona 1:3–1:5.
- El ArUco se mantiene para compensar el backlash.

**Conceptos de SO demostrables:** IRQ, debounce con timers de alta resolución, `kfifo`, wait queues, `poll`, `ioctl`, `sysfs`, device tree, concurrencia y diseño de una interfaz extensible a otros dispositivos.

### `device-agent` (Python en la Pi)

**Mapeo de los botones** (lo aplica el host, no el agente: el agente reenvía los eventos crudos)

| GPIO (BCM) | Código | Acción |
|---|---|---|
| 4 | `BTN_RIGHT` | Yaw continuo a la derecha (mantener) |
| 27 | `BTN_LEFT` | Yaw continuo a la izquierda (mantener) |
| 22 | `BTN_ZOOM_IN` | Zoom + (mantener) |
| 23 | `BTN_ZOOM_OUT` | Zoom − (mantener) |
| 24 | `BTN_AUTOROTATE` | Activar o desactivar la rotación automática |

Reset de vista y siguiente modelo no tienen botón: solo existen en el teclado del host.

**El agente:**
- lee `/dev/holo-input` y envía cada registro al host como `INPUT_EVENT`, por mTLS y sin interpretarlo; los eventos que se producen con el enlace caído se descartan (el host corta solo un movimiento cuando dejan de llegar repeticiones);
- recibe `CameraState` y se lo pasa al renderer local por su socket Unix; un renderer que se conecta recibe de inmediato el último estado;
- recibe `MODEL_PUSH` (LOD-low), verifica tamaño y SHA-256, lo instala de forma atómica como `<models-dir>/<modelId>.glb` y repite el último estado para que el renderer cargue un modelo que antes no existía;
- reintenta abrir el dispositivo si no está disponible, y se reconecta solo al host;
- *(opcional)* ejecuta `ROTATE` y `ROTATE_DONE` cuando existe la tornamesa.

---

## Red y seguridad (`common/`)

**Transporte**
- TCP con **TLS 1.3 y autenticación mutua (mTLS)**.
- CA propia (`pki/make-certs.sh`, con OpenSSL) y un certificado por nodo.
- Se rechaza cualquier conexión sin certificado válido.
- ECDHE da forward secrecy.

**Protocolo:** framing `[u32 length][u8 type][payload msgpack]`, con estos mensajes:
- `HELLO`, `JOB_ASSIGN`, `JOB_RESULT`, `HEARTBEAT`;
- `INPUT_EVENT`, `CAMERA_STATE`;
- `MODEL_PUSH` (en chunks con hash SHA-256);
- *(opcional)* `ROTATE` / `ROTATE_DONE`.

**Nodos:** lista estática en `config.toml`.

---

## Estructura del repositorio (código en inglés, kebab-case)

```
astrea-view/
  driver/holo-io/          # holo-core.c, holo-buttons.c, holo-io.h, holo-event.h, holo-ioctl.h, holo-reader.c, zx-gesture.c*, turntable.c*, holo-io-overlay.dts, 99-holo-io.rules, Makefile
  device-agent/            # /dev/holo-input reader, mTLS client, renderer socket, model installer
  host/capture-service/    # record3d / .r3d ingestion, ArUco tracking, keyframe selection
  host/coordinator/        # scheduling, TSDF merge, meshing, LOD export, camera state (camera_control)
  worker/                  # partial TSDF fusion
  renderer/                # C + raylib, viewer/hologram modes
  common/                  # protocol, TLS context, config
  pki/                     # CA + node certs
  hardware/                # ArUco ring generator, 3D-printable plate/coupler (optional)
  tests/                   # synthetic pipeline, protocol, driver tests
  docs/                    # IEEE-Trans paper (español), diagrams
```
`*` = fase opcional.

---

## Fases (los riesgos primero)

**0. Spikes de viabilidad**
- a) Record3D → RGB-D en Python en Linux, o parsear un `.r3d`.
- b) Pose con el anillo ArUco sobre la base manual, en el RGB de Record3D.
- c) raylib en DRM sobre la Pi 3 a ≥30 fps con 8k triángulos.
- d) Prueba de reflejo con el monitor y el panel.

**1. Driver:** `/dev/holo-input` con los 5 botones (IRQ, debounce, autorepeat, `poll`, `ioctl`, `sysfs`), su overlay de DT y la carga al arrancar.

**2. Red segura:** PKI, framing, mTLS entre los 3 nodos y heartbeat.

**3. Tubería en un nodo:** TSDF + mallado + LOD. Primero se valida con la prueba sintética y luego con capturas reales con ArUco.

**4. Distribución:** reparto por rendimiento, merge exacto, reasignación ante fallos y medición de 1 vs 3 workers.

**5. Renderer:** modos viewer y hologram, sincronización de `CameraState` y mapeo de los botones y del teclado.

**6. Maqueta:** caja Pepper's Ghost, montaje de los botones, base manual con ArUco.

**7. Documentación:** secciones IEEE y los diagramas (arquitectura, componentes, secuencias de escaneo y de botones→holograma, funcionalidades). Evidencias de aprendizaje continuo.

**8. Opcionales**, en orden:
- a) Tornamesa motorizada: `/dev/turntable` + plato impreso + `ROTATE` en `capture-service`.
- b) ZX Gesture Sensor sobre `/dev/holo-input`.

---

## Verificación

**Driver**
- `insmod` / `dmesg` limpios.
- Un lector de prueba imprime los eventos de los botones.
- Pulsar 100 veces un botón da exactamente 100 eventos (el debounce funciona); las estadísticas de `sysfs` coinciden.
- `rmmod` sin fugas de IRQ ni de GPIO.

**Captura:** el error de ángulo de ArUco es < 1° contra posiciones marcadas en el plato.

**Tubería**
- La distancia de Chamfer de la prueba sintética queda bajo el umbral.
- Las mallas reales respetan el presupuesto de triángulos y tienen una sola componente.

**Distribución**
- 3 workers dan el mismo resultado que 1 (tolerancia ε).
- Se reporta el speedup.
- Si se mata un worker a mitad del trabajo, la reconstrucción igual termina.

**Seguridad**
- Wireshark muestra solo TLS 1.3.
- Se rechaza un cliente sin certificado o con uno de otra CA.

**Fin a fin:** girar el objeto a mano → captura automática → modelo en el visor → aparece en el holograma → los botones rotan y hacen zoom en ambos sincronizados, con latencia del botón al holograma < 100 ms.

## Confirmado
- Segundo computador disponible como worker B.
- iPhone con TrueDepth.
- Se compra el streaming USB de Record3D (`.r3d` queda solo como respaldo).

## Pendientes a confirmar
- Fecha de entrega y tamaño del equipo.
