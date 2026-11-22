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
- **Control:** un **joystick digital tipo arcade por GPIO**. El ZX Gesture Sensor queda como **fase opcional final**.
- **Holograma:** un solo panel a 45° sobre un monitor viejo.

---

## Evaluación crítica

| Problema | Por qué | Solución adoptada |
|---|---|---|
| App iOS propia sin Mac | Xcode es obligatorio. Compilar en CI macOS y hacer sideload da certificados de 7 días y no hay depurador. | Record3D transmite el RGB-D del TrueDepth. El trabajo propio está en la tubería. |
| Pose de cada vista sin motor | Si la base se gira a mano, no se conoce el ángulo. Registrar las vistas con ICP es frágil con el ruido del TrueDepth. | Un **anillo de marcadores ArUco** en el borde del plato da la pose en cada frame, aunque el objeto tape algunos marcadores. Se captura automáticamente cada ~10°. |
| Área de "Hardware" sin motor | La especificación pide "movimiento o procesamiento interesante". | El holograma y el renderer cubren la parte de procesamiento. La tornamesa motorizada queda lista para agregarse y reforzar el área. **Riesgo:** si no se implementa, el argumento de hardware se apoya en el display y el joystick. |
| Enviar la visualización como video | Agrega latencia y carga en la Pi 3. | Se sincroniza solo el **estado de cámara**. La Pi renderiza localmente el modelo, que recibe cifrado una sola vez. |
| Distribución débil | El teléfono no procesa nada propio. | La reconstrucción se **paraleliza por vistas** en ≥3 workers y se combina de forma exacta. |
| Tamaño variable del modelo | Una malla sin control puede llegar a cientos de miles de vértices con ruido. | Una rejilla de vóxeles fija, limpieza, decimación a un presupuesto de triángulos y dos niveles de detalle (LOD). |

---

## Arquitectura

```
 iPhone (Record3D, TrueDepth) ──USB/Wi-Fi──► HOST PC
                                           ├─ capture-service  (tracking ArUco, captura automática)
                                           ├─ coordinator      (reparte trabajos, fusiona, malla)
                                           ├─ worker
                                           └─ renderer --mode viewer
                     mTLS (TCP)                 ▲        ▲
        ┌──────────────────────────────────────┘        └───────────┐
   WORKER B (laptop del equipo)                         RASPBERRY PI 3 (dispositivo)
   └─ worker                                            ├─ módulo de kernel holo-io
                                                        │   ├─ /dev/holo-input  (joystick GPIO; ZX después)
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
- **Cámara orbital:** yaw, pitch y zoom, más autorrotación opcional.
- **Modo hologram:** fondo negro, imagen espejada según la orientación del panel e iluminación alta.
- **El host es la fuente de verdad del estado de cámara.**
  - Combina el mouse local con los eventos del joystick remoto.
  - Emite `CameraState{yaw, pitch, zoom, modelId}` cuando cambia, a ≤60 Hz.
- **Socket Unix local** entre el proceso Python y el renderer. No necesita cifrado porque no pasa por la red.

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
| Joystick arcade digital (4 microswitches) + 4 botones | ~$5–10 |
| Base giratoria manual: plato de 15–20 cm, superficie mate clara, anillo ArUco impreso | ~$0–3 |
| *(Opcional)* 28BYJ-48 + ULN2003 + lazy susan + plato impreso en 3D | ~$6 |
| *(Opcional)* ZX Gesture Sensor | ~$25 |

### Driver: módulo de kernel `holo-io` (C)

**Overlay de device tree**
- Declara los GPIO del joystick (4 direcciones + 4 botones) con `bias-pull-up`.
- Reserva los nodos del ZX (I2C1 `0x10`, pin DR) y del stepper (4 GPIO), deshabilitados (`status = "disabled"`) hasta su fase.
- El código usa la API `gpiod_*`.

**`/dev/holo-input`** (char device; una sola interfaz para todas las entradas)
- **Formato de evento:** `struct HoloEvent{source, code, value, timestampNs}`. Hoy `source` es `JOYSTICK`; más adelante también `ZX_GESTURE`, sin cambiar el agente.
- **Joystick:**
  - IRQ por ambos flancos en cada GPIO.
  - **Debounce** con `hrtimer` (~5–10 ms) antes de leer el estado estable.
  - **Autorepeat** mientras una dirección se mantiene presionada (`hrtimer` periódico), para dar rotación continua.
- **Interfaz con el usuario del dispositivo:**
  - eventos encolados en un `kfifo` con `wait_queue`;
  - `read()` / `poll()`;
  - `ioctl` para configurar el debounce y la tasa de repetición;
  - estadísticas en `sysfs` (IRQs, rebotes filtrados, eventos).
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

**Mapeo del joystick**

| Entrada | Acción |
|---|---|
| ←/→ (mantener) | Yaw continuo |
| ↑/↓ | Pitch |
| Botón A / B | Zoom + / − |
| Botón C | Reset de vista |
| Botón D | Siguiente modelo |

**Además, el agente:**
- envía las acciones al host por mTLS;
- recibe `CameraState` y los modelos LOD-low y se los pasa al renderer local;
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
  driver/holo-io/          # holo-io.c, holo-joystick.c, zx-gesture.c*, turntable.c*, holo-io-overlay.dts, Makefile
  device-agent/            # input mapping, mTLS client
  host/capture-service/    # record3d / .r3d ingestion, ArUco tracking, keyframe selection
  host/coordinator/        # scheduling, TSDF merge, meshing, LOD export
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

**1. Driver:** `/dev/holo-input` con el joystick (IRQ, debounce, autorepeat, `poll`, `ioctl`, `sysfs`), su overlay de DT y la carga al arrancar.

**2. Red segura:** PKI, framing, mTLS entre los 3 nodos y heartbeat.

**3. Tubería en un nodo:** TSDF + mallado + LOD. Primero se valida con la prueba sintética y luego con capturas reales con ArUco.

**4. Distribución:** reparto por rendimiento, merge exacto, reasignación ante fallos y medición de 1 vs 3 workers.

**5. Renderer:** modos viewer y hologram, sincronización de `CameraState` y mapeo del joystick.

**6. Maqueta:** caja Pepper's Ghost, montaje del joystick, base manual con ArUco.

**7. Documentación:** secciones IEEE y los diagramas (arquitectura, componentes, secuencias de escaneo y de joystick→holograma, funcionalidades). Evidencias de aprendizaje continuo.

**8. Opcionales**, en orden:
- a) Tornamesa motorizada: `/dev/turntable` + plato impreso + `ROTATE` en `capture-service`.
- b) ZX Gesture Sensor sobre `/dev/holo-input`.

---

## Verificación

**Driver**
- `insmod` / `dmesg` limpios.
- Un lector de prueba imprime los eventos del joystick.
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

**Fin a fin:** girar el objeto a mano → captura automática → modelo en el visor → aparece en el holograma → el joystick rota y hace zoom en ambos sincronizados, con latencia del joystick al holograma < 100 ms.

## Confirmado
- Segundo computador disponible como worker B.
- iPhone con TrueDepth.
- Se compra el streaming USB de Record3D (`.r3d` queda solo como respaldo).

## Pendientes a confirmar
- Fecha de entrega y tamaño del equipo.
