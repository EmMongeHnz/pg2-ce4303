# Protocolo de red de AstreaView

Especificación del transporte seguro y de los mensajes entre los nodos (M2). **Toda la red segura está implementada en C** (`net/`, sobre OpenSSL): `astrea-netd`, el demonio de red de cada nodo, y `astrea-device-agent`, el agente de la Pi, comparten la biblioteca estática `libastrea-net.a` (TLS 1.3 con autenticación mutua, framing, `HELLO`, reglas de mensajes, latidos y reconexión). Los programas Python (workers, coordinador, `astrea-host`) no abren sockets de red: arrancan el demonio y le entregan o reciben mensajes completos por un socket Unix local (sección 5, «Demonio de red»). Los mensajes y sus campos se definen y validan en `common/src/astrea_common/messages.py`, y la verificación está en `astrea-net-check` y en las pruebas de `tests/unit/`.

## 1. Topología

El host PC es el **hub** y tiene dos oyentes independientes, para que dos procesos separados no se disputen el puerto: el de las **reconstrucciones** (`hub.port`, por defecto 7400), que abre `astrea-coordinator reconstruct` mientras corre y al que se conectan los workers, y el del **dispositivo** (`hub.device-port`, por defecto `hub.port + 1`, es decir 7401), que mantiene el servicio permanente `astrea-host` y al que se conecta el `device-agent` de la Pi. Ambos usan el mismo certificado, el mismo protocolo y las mismas reglas. La Pi puede conectarse por un cable Ethernet directo al host: es solo la capa física, el protocolo y el cifrado no cambian. Worker B y la Pi **abren la conexión hacia el hub**; no hay enlaces entre ellos. Cada conexión es bidireccional y persistente, y cada nodo se reconecta con espera exponencial (`reconnect-initial-s` a `reconnect-max-s`).

Un mismo nodo puede mantener una conexión por rol (la Pi abre una como `worker` y otra como `device-agent`; el host puede abrir una como `worker` hacia su propio hub). El hub guarda una sola conexión viva por pareja (nodo, rol): una conexión nueva reemplaza a la anterior.

| Rol | Quién lo usa | Mensajes que envía |
|---|---|---|
| `coordinator` | solo el hub (host) | `JOB_ASSIGN`, `CAMERA_STATE`, `MODEL_PUSH` |
| `worker` | host, worker B, Pi | `JOB_RESULT` |
| `device-agent` | Pi | `INPUT_EVENT` |

## 2. Transporte y seguridad

- TCP con **TLS 1.3 únicamente** (versión mínima y máxima fijadas) y **autenticación mutua obligatoria**: el servidor exige certificado de cliente y ambos lados confían **solo** en `ca.crt` (nunca en el almacén del sistema).
- PKI propia generada con `pki/make-certs.sh`: CA ECDSA P-256 y un certificado por nodo con `extendedKeyUsage = serverAuth, clientAuth`. El **nombre del nodo** (`host`, `worker-b`, `pi`) va en el SAN DNS.
- Intercambio de claves efímero (ECDHE), con *forward secrecy*.
- **Identidad del hub:** el cliente verifica el certificado del servidor contra el nombre del hub (`hub.node`), aunque se conecte por IP. Un certificado de otro nodo no se acepta como hub.
- **Identidad del cliente:** el hub lee el nombre del SAN del certificado y exige que coincida con el `node` de `HELLO` y que el rol esté permitido para ese nodo en `config.toml`.
- **Cifrado:** los tres suites TLS 1.3 son AEAD. Por defecto el nodo ofrece `TLS_AES_256_GCM_SHA384` primero; con `--prefer-chacha20` (la Pi 3 no tiene aceleración AES) ofrece `TLS_CHACHA20_POLY1305_SHA256` primero y es el que se negocia, porque el servidor sigue la preferencia del cliente.
- **Identidad única:** el certificado debe llevar **un solo** nombre DNS; uno que nombre a varios nodos se rechaza como identidad ambigua.
- El socket Unix entre el proceso Python local y el renderer no sale de la máquina y no usa TLS (formato en la sección 5, «Socket Unix del renderer»).

## 3. Framing

```
[u32 length][u8 type][payload msgpack]
```

- `length`: big-endian, cuenta `type` + `payload` (mínimo 1).
- `type`: valor de `MessageType` (sección 5).
- `payload`: un mapa msgpack. Las claves desconocidas se ignoran.
- Tope de trama: 4 MiB. Se rechaza antes de reservar memoria. Los emisores deben apuntar a ≤ 1 MiB por trama: los trabajos y resultados grandes se parten en varias tramas (`part` / `parts`, como mínimo una vista o un bloque por trama) y los modelos en chunks de 1 MiB. Así un envío largo no retiene el canal más que un instante y los `HEARTBEAT` se intercalan entre tramas.
- Se cierra la conexión ante: longitud 0 o sobre el tope, tipo desconocido, msgpack inválido o payload que no es un mapa, campo faltante o de tipo incorrecto.

## 4. Conexión

1. El nodo abre TLS 1.3 con su certificado y envía `HELLO {protocolVersion, node, role}`.
2. El hub valida: versión igual a `PROTOCOL_VERSION`, `node` igual al SAN del certificado, `role` distinto de `coordinator` y permitido para ese nodo. Si es válido responde `HELLO {protocolVersion, node = hub.node, role = "coordinator"}`; si no, cierra sin responder y lo registra.
3. El nodo valida la respuesta (nombre del hub y rol `coordinator`).
4. Desde ahí cada lado envía `HEARTBEAT {sequence, sentAtNs, echo = false}` cada `heartbeat-interval-s`. El receptor responde el mismo mensaje con `echo = true`; quien lo originó calcula el RTT con su propio reloj monotónico (`sentAtNs` solo tiene sentido para quien lo envió).
5. Si no llega ningún byte en `heartbeat-timeout-s` (se cuenta cada fragmento recibido, no solo las tramas completas, para no confundir una trama grande en un enlace lento con un nodo caído), se cierra la conexión. El hub lo notifica y, en M4, el trabajo de ese worker se reasigna.

`HELLO` fuera del inicio, o cualquier mensaje que viole la tabla de la sección 6, cierra la conexión.

## 5. Mensajes

| Tipo | Valor | Payload |
|---|---|---|
| `HELLO` | 1 | `protocolVersion` int, `node` str, `role` str |
| `JOB_ASSIGN` | 2 | `jobId` str, `part`/`parts` int, `grid {originMm[3], voxelMm, shape[3]}`, `truncationMm`, `params` mapa (overrides de `PreprocessParams` y opciones TSDF), `views[]` |
| `JOB_RESULT` | 3 | `jobId` str, `part`/`parts` int, `status` (`ok` / `error`), `error` str, `elapsedS` (segundos de fusión), `viewIndices[]`, `blocks[]`, `keptPercent[]` (opcional, porcentaje de profundidad que conserva el preprocesado por vista, en el mismo orden que `viewIndices`) |
| `HEARTBEAT` | 4 | `sequence` int, `sentAtNs` int, `echo` bool |
| `INPUT_EVENT` | 5 | `source`, `code`, `value`, `timestampNs` (espejo de `struct HoloEvent`; `source`: 1 botones GPIO, 2 gesto ZX; valores de `code` y `value` en la subsección N3) |
| `CAMERA_STATE` | 6 | `yaw`, `pitch`, `zoom` float, `modelId` str (`pitch` es fijo, 20°) |
| `MODEL_PUSH` | 7 | `modelId`, `index`, `count`, `totalSize`, `sha256` (hex del modelo completo tal como se transmite), `compression` (`none` / `zstd`), `data` bin |
| `ROTATE` | 8 | `requestId`, `steps` (definido, deshabilitado) |
| `ROTATE_DONE` | 9 | `requestId`, `positionSteps` (definido, deshabilitado) |

Sub-estructuras:
- **`views[]`** (de `JOB_ASSIGN`): `index`, `intrinsics {fx, fy, cx, cy, width, height}`, `rotation[9]` (fila mayor) y `translationMm[3]` con `xCamera = rotation · xPlate + translationMm`, `rgb` y `depthM` como `ArrayBlob`.
- **`blocks[]`** (de `JOB_RESULT`): `startIndex[3]` y `shape[3]` de una caja de vóxeles con peso > 0, y `tsdf`, `weight`, `color` como `ArrayBlob`.
- **`ArrayBlob`**: `dtype` (enteros o flotantes de hasta 8 bytes, little endian), `shape` (1 a 4 dimensiones), `compression` (`none` / `zstd`), `data`. La descompresión está acotada por el tamaño que declara `shape`.
- **Modelos:** el receptor junta los chunks en cualquier orden y acepta el modelo solo si el tamaño y el SHA-256 coinciden (`ModelAssembler`).

### Entradas y estado de cámara (N3)

- **Botones GPIO** (`source = 1`; `driver/holo-io/holo-event.h` es la fuente de verdad y `astrea_common.holo_event` lo espeja):

  | `code` | Botón | GPIO (BCM) | Acción |
  |---|---|---|---|
  | 1 | `BTN_RIGHT` | 4 | yaw a la derecha mientras se mantiene |
  | 2 | `BTN_LEFT` | 27 | yaw a la izquierda mientras se mantiene |
  | 3 | `BTN_ZOOM_IN` | 22 | zoom + mientras se mantiene |
  | 4 | `BTN_ZOOM_OUT` | 23 | zoom − mientras se mantiene |
  | 5 | `BTN_AUTOROTATE` | 24 | activa o desactiva la rotación automática |

- **`value`:** 0 suelta, 1 pulsa, 2 repite. El driver repite los cuatro botones de movimiento mientras se mantienen; el de autorrotación no repite y solo actúa al pulsar. El host da por terminado un movimiento si dejan de llegar repeticiones durante 0.3 s, de modo que un evento de soltar perdido no deja la cámara moviéndose.
- **El host es la única fuente de verdad.** Integra botones, teclado (A/D yaw, W/S zoom, `R` autorrotación, `N` modelo siguiente) y mouse (arrastre horizontal) en un solo estado; las contribuciones se suman. No hay rotación vertical ni reset.
- **Autorrotación:** la integra el host (20°/s, empieza desactivada) y llega a los visores dentro de `yaw`; ningún renderer gira por su cuenta cuando lo controla el host.
- **`CAMERA_STATE`:** se emite cuando algo cambia, a ≤60 Hz, y siempre con el estado final tras un movimiento. `pitch` se conserva en el mensaje con el valor fijo 20.0. No hay cambios en el formato de los mensajes, solo en los valores que llevan.

### Interfaz del driver `/dev/holo-input` (Pi, entre el driver y el `device-agent`)

No viaja por la red: es el contrato local entre `holo-io` y el agente, que reenvía cada registro como `INPUT_EVENT`. Fuentes de verdad: `driver/holo-io/holo-event.h` y `driver/holo-io/holo-ioctl.h`.

- **Registro:** `struct HoloEvent` de 16 bytes, sin relleno y en el orden de la CPU (little-endian en la Pi 3 y en x86): `timestampNs` u64 (reloj monotónico de la Pi, `ktime_get_ns`), `source` u16, `code` u16, `value` s32.
- **Apertura exclusiva:** un solo lector a la vez; un segundo `open` falla con `EBUSY`. Al abrir se descartan los eventos acumulados.
- **`read()`:** devuelve uno o más registros completos (hasta 16 por llamada); un búfer menor a 16 bytes da `EINVAL`. Sin eventos bloquea, o responde `EAGAIN` con `O_NONBLOCK`. Si el dispositivo desaparece responde `ENODEV`.
- **`poll()`:** `POLLIN` cuando hay eventos y `POLLHUP` si el dispositivo se está retirando.
- **Cola:** `kfifo` de 256 registros. Si se llena, el evento nuevo se descarta y se cuenta en `dropped`.
- **Orden:** una pulsación genera `PRESS`; mientras se mantiene un botón de movimiento llegan `REPEAT` y al soltar `RELEASE`. Nunca llega un `REPEAT` después de su `RELEASE`.
- **`ioctl`** (magia `'H'`, valores `__u32` en milisegundos):

  | Orden | Número | Rango | Por defecto |
  |---|---|---|---|
  | `HOLO_IOC_GET_DEBOUNCE_MS` / `HOLO_IOC_SET_DEBOUNCE_MS` | 1 / 2 | 1–100 | 10 |
  | `HOLO_IOC_GET_REPEAT_MS` / `HOLO_IOC_SET_REPEAT_MS` | 3 / 4 | 10–100 | 50 |

  Un valor fuera de rango da `EINVAL`. La primera repetición llega un periodo después de la pulsación, y el máximo de 100 ms deja al menos 3 repeticiones dentro del corte de 0.3 s del host (`BUTTON_HOLD_TIMEOUT_S`, comprobado en `tests/unit/test_holo_io_sources.py`).
- **Estadísticas** en `/sys/class/misc/holo-input/device/stats/` (solo lectura): `irqs` (flancos), `presses`, `releases`, `repeats`, `events` (suma de las tres), `bounces_filtered` (flancos que no terminaron en pulsación ni suelta, es decir `irqs − presses − releases`), `dropped`, `debounce_ms` y `repeat_ms`.
- **Criterio «N pulsaciones = N eventos»:** se cuenta en eventos `PRESS`. Con el botón de autorrotación, que no repite, `presses` aumenta en N, el lector ve N `PRESS` y `dropped` no cambia (`libholo/tools/check-presses.sh`).
- **Biblioteca:** el único código que abre `/dev/holo-input`, emite sus `ioctl` o lee sus estadísticas es `libholo` (`libholo/include/holo.h`, `libholo.a`): `HoloOpen`, `HoloClose`, `HoloReadEvent` (con tiempo de espera; reconstruye los registros cortados), `HoloGetDebounceMs`, `HoloSetDebounceMs`, `HoloGetRepeatMs`, `HoloSetRepeatMs`, `HoloReadStats` y `HoloActionOf` (convierte un botón en `MoveRight`, `MoveLeft`, `ZoomIn`, `ZoomOut` o `ToggleAutoRotate` con su fase). El `device-agent` en C y `holo-reader` la usan; una prueba verifica que ningún otro archivo del repositorio usa los `ioctl` ni el sysfs del driver.
- **Acceso:** la regla udev `99-holo-io.rules` deja el nodo en el grupo `input` con modo 0660.

### Demonio de red (local, entre el programa Python y `astrea-netd`)

No viaja por la red. `astrea-netd --mode hub|node` hace toda la red cifrada y habla con el programa local por un socket Unix (permisos 0600) con marcos `[u32 longitud][u8 tipo][cuerpo]` en big-endian. El cliente local es uno solo; si se conecta otro, reemplaza al anterior y recibe de nuevo los enlaces activos.

| Del demonio al programa | Cuerpo |
|---|---|
| 1 `PEER_UP` | `id` u32, nodo (u8 + texto), rol (u8 + texto), sesión TLS (u16 + texto) |
| 2 `PEER_DOWN` | `id` u32, motivo |
| 3 `MESSAGE` | `id` u32, tipo u8, carga msgpack tal como llegó |
| 4 `RTT` | `id` u32, microsegundos u32 |
| 5 `REJECT` / 6 `FATAL` | texto |

| Del programa al demonio | Cuerpo |
|---|---|
| 1 `SEND` | `id` u32 (0 = el único enlace de un nodo), tipo u8, carga msgpack |
| 2 `CLOSE` | `id` u32, motivo |
| 3 `SHUTDOWN` | — |

El demonio aplica en el cable todo lo de las secciones 3, 4 y 6: tope de 4 MiB por trama, tipos válidos, carga msgpack bien formada, reglas de quién envía qué, `HELLO` solo al inicio, latidos y corte por silencio. `HEARTBEAT` y `HELLO` nunca llegan al programa. Hay un demonio por enlace: `--link reconstruction` escucha en `hub.port` y `--link device` en `hub.device-port`; un nodo con un rol usa el puerto de ese rol. Con `--exit-on-detach` el demonio termina cuando el programa se desconecta. El programa Python que lo lanza escribe un `config.toml` temporal con rutas absolutas.

### Socket Unix del renderer (local, no viaja por la red)

Contrato entre el proceso Python de una máquina y su `renderer` (`--control-socket <ruta>`). El proceso Python escucha, el renderer se conecta como cliente y reintenta cada 0.5 s. Es un flujo de registros binarios de tamaño fijo, en el orden de bytes de la máquina (little-endian en la Pi 3 y en x86). Fuentes de verdad: `renderer/src/control-protocol.h` y su espejo `astrea_common.render_link`, comparados por `tests/unit/test_render_link.py`.

- **Python → renderer, `ControlState` (44 bytes):** `yawDeg`, `pitchDeg`, `zoom` (float32) y `modelId` (32 bytes, UTF-8 rellenado con ceros; 31 como máximo). Es el estado que dibuja el renderer; el mismo que `CAMERA_STATE`.
- **Renderer → Python, `ControlInput` (8 bytes):** `kind` u8, `action` u8, `reserved` u16 (0) y `value` float32.

  | `kind` | Significado |
  |---|---|
  | 1 | `ACTION_PRESS`: se pulsó la tecla de `action` |
  | 2 | `ACTION_RELEASE`: se soltó |
  | 3 | `MOUSE_DELTA`: arrastre horizontal en píxeles (`value`) |

  | `action` | Tecla |
  |---|---|
  | 1 `YAW_LEFT` / 2 `YAW_RIGHT` | `A` / `D` (mantener) |
  | 3 `ZOOM_IN` / 4 `ZOOM_OUT` | `W` / `S` (mantener) |
  | 5 `TOGGLE_AUTO_ROTATE` | `R` (solo `PRESS`) |
  | 6 `NEXT_MODEL` | `N` (solo `PRESS`) |

- **Comportamiento:** con el socket conectado, el renderer no mueve la cámara ni gira por su cuenta: solo reporta entradas y dibuja el último estado. Sin conexión conserva el último estado y reintenta. El renderer en modo `hologram` (la Pi) no reporta entradas. Si el Python se desconecta, suelta las teclas que tenía pulsadas (`ReleaseDevice`).
- **`modelId`:** el renderer carga `<--models-dir>/<modelId>.glb`, salvo el modelo inicial (el nombre de `--model` sin `.glb`), que recarga desde la ruta con la que arrancó aunque no esté en ese directorio; el id solo admite letras, dígitos, `-`, `_` y `.` (sin empezar por punto), así que no puede salir del directorio. Si el archivo no existe conserva el modelo actual y lo avisa una sola vez. Un id vacío no cambia nada.
- **Lado Python:** `coordinator.renderer_bridge.RendererBridge` atiende el socket, alimenta a `CameraControl` y envía el estado a ≤60 Hz (descarta envíos si el renderer va atrasado: gana el último).

### Comportamiento del `device-agent` (N2 y N3)

- **`INPUT_EVENT`:** cada registro de `/dev/holo-input` se envía tal cual (`source`, `code`, `value`, `timestampNs` de la Pi). El agente no interpreta los botones: el host los convierte en acciones de cámara. Con el enlace caído los eventos se descartan.
- **`CAMERA_STATE`:** se entrega al renderer local como `ControlState`. El agente guarda el último y se lo envía a un renderer que se conecta o se reconecta.
- **`MODEL_PUSH`:** un modelo es válido si el id cumple `[A-Za-z0-9_-][A-Za-z0-9_.-]*` con 31 bytes como máximo, `totalSize` ≤ 128 MiB, `compression = none` y el SHA-256 de lo transmitido coincide. El agente en C no descomprime zstd: un modelo con otra compresión se rechaza y se registra. Se instala como `<models-dir>/<modelId>.glb` (archivo temporal más `rename`, así el renderer nunca ve un archivo a medias). Una transferencia nueva del mismo id reemplaza a una incompleta, y los trozos pueden llegar en cualquier orden.
- **Tras instalar un modelo** el agente reenvía el último `CAMERA_STATE` si ese estado nombra el modelo, porque el renderer solo reintenta cargar cuando recibe un estado.

### Trabajos de reconstrucción (N1)

- **`params`** de `JOB_ASSIGN`: `preprocess` (mapa con campos de `PreprocessParams`; los que faltan toman su valor por defecto y una clave desconocida se rechaza), `weights` y `bilinear` (booleanos). `truncationMm` va aparte, en el mismo mensaje.
- **Partes:** un trabajo se envía en varias tramas `JOB_ASSIGN` de ~1 MiB con el mismo `jobId` (`part` de 0 a `parts - 1`, hasta 4096). El worker empieza cuando llegaron todas. El resultado vuelve igual en tramas `JOB_RESULT`; `viewIndices` y `keptPercent` viajan en la parte 0.
- **Un trabajo a la vez por worker:** un trabajo que llega mientras otro sigue calculando (por ejemplo, uno que dejó un coordinador caído) espera su turno. Si llegan partes de otro `jobId` antes de completar uno, o se cae el enlace a medias, el trabajo incompleto se descarta. Un trabajo que no cabe en memoria o con parámetros inválidos responde `JOB_RESULT` con `status = error`.
- **Fallos y reasignación:** el coordinador da por caído a un worker que se desconecta durante el trabajo, responde `status = error`, devuelve estadísticas que no corresponden a sus vistas o no termina en `--job-timeout` segundos. Ese worker queda fuera durante el resto de la corrida, sus vistas se reparten en una ronda nueva entre los workers que siguen conectados y el resultado se fusiona igual, porque la fusión no depende de qué worker procesó cada vista. Solo falla la corrida cuando no queda ningún worker, y el mensaje dice cuántas vistas quedaron sin fusionar y por qué falló cada uno. Un resultado tardío de un worker descartado se ignora.
- **Resultado:** solo las teselas de 32³ vóxeles con algún peso > 0, con `startIndex` en la rejilla común. Las matrices viajan sin pérdida (profundidad en float32), de modo que el coordinador obtiene el mismo volumen que una fusión en un nodo.
- **Fusión en el coordinador:** `D = Σ wᵢ dᵢ / Σ wᵢ`, y lo mismo para el color, bloque a bloque sobre un único volumen acumulador. Los bloques de un worker se aplican solo cuando llegaron todas sus partes.

## 6. Quién puede enviar qué

| Mensaje | Lo envía | Rol del nodo en la conexión |
|---|---|---|
| `HELLO`, `HEARTBEAT` | cualquiera | cualquiera |
| `JOB_ASSIGN` | hub | `worker` |
| `JOB_RESULT` | nodo | `worker` |
| `INPUT_EVENT` | nodo | `device-agent` |
| `CAMERA_STATE`, `MODEL_PUSH` | hub | `device-agent` |
| `ROTATE`, `ROTATE_DONE` | — | deshabilitados hasta la fase 8 |

Se comprueba al enviar y al recibir.

## 7. Correspondencia con el diagrama de bloques

| Etiqueta | Mensajes | Enlace |
|---|---|---|
| N1 | `JOB_ASSIGN` / `JOB_RESULT` | coordinator ↔ workers |
| N2 | `MODEL_PUSH` | coordinator → Pi |
| N3 | `INPUT_EVENT` / `CAMERA_STATE` | Pi ↔ host |
| N4 | `ROTATE` / `ROTATE_DONE` | opcional (fase 8) |

`HELLO` y `HEARTBEAT` corren en todos los enlaces. Todas las aristas pasan por el hub.

## 8. Versionado

`PROTOCOL_VERSION = 1`. Se pueden añadir campos **opcionales** a un mensaje sin cambiar la versión (los decodificadores ignoran claves desconocidas). Cualquier otro cambio (campo obligatorio nuevo, tipo nuevo, cambio de significado o de framing) sube la versión y se rechaza entre nodos de versiones distintas. El protocolo se propone como congelado al cerrar M2, a la espera de acuerdo del equipo.

## 9. `config.toml`

Mismo archivo en todas las máquinas. Las rutas relativas se resuelven desde la carpeta del archivo y `{node}` se reemplaza por el nombre del nodo que corre en esa máquina, de modo que cada una solo necesita su propia clave.

```toml
[hub]
node = "host"
address = "192.168.1.10"       # lo que marcan los nodos
bind-address = "0.0.0.0"       # donde escucha el hub (por defecto igual a address)
port = 7400
device-port = 7401             # opcional (por defecto port + 1): hub de astrea-host y del device-agent

[tls]
ca-file = "pki/out/ca.crt"
cert-file = "pki/out/{node}.crt"
key-file = "pki/out/{node}.key"

[nodes.host]
roles = ["coordinator", "worker"]
[nodes.worker-b]
roles = ["worker"]
[nodes.pi]
roles = ["worker", "device-agent"]
```

La sección `[network]` (heartbeat, timeouts, reconexión) es opcional. Claves desconocidas, roles inválidos o un hub sin rol `coordinator` se rechazan al cargar.

## 10. Prueba en las máquinas reales (M2.6)

Esto es lo único de M2 que falta ejecutar: la lógica ya se verifica en loopback con `uv run pytest`.

**Preparación (en el host):**
1. `pki/make-certs.sh --out pki/out --ip host=<IP del host>` (genera la PKI de la demo; no usar `pki/example/`).
2. Copiar a cada nodo solo `ca.crt` y su par `<node>.crt` / `<node>.key` (por ejemplo `scp`) a `pki/out/`, junto con el mismo `config.toml`.
3. En `config.toml`: `hub.address = <IP del host>` y `hub.bind-address = "0.0.0.0"`. Abrir los puertos 7400/TCP (workers) y 7401/TCP (`astrea-host` y `device-agent`) en el host.
4. En el host, worker B y la Pi: `git clone` del repositorio, `sudo apt install build-essential libssl-dev`, `make -C net` (compila el demonio de red y el agente en C) y `uv sync`.

**Enlace de los tres nodos:**
```
# host
uv run astrea-net-check serve --expect worker-b pi:device-agent pi:worker --duration 120

# worker B (sin --duration: corre hasta que el hub cierre el enlace; se pueden lanzar en cualquier orden y con minutos de diferencia)
uv run astrea-net-check connect --name worker-b

# Pi, ofreciendo ChaCha20-Poly1305 primero (un proceso por rol)
uv run astrea-net-check connect --name pi --role device-agent --prefer-chacha20
uv run astrea-net-check connect --name pi --role worker --prefer-chacha20
```
Resultado esperado: el host imprime `TLSv1.3` y un RTT por cada nodo, y termina con `all expected nodes are connected and exchanging heartbeats` (código 0). Tras 5 s (`--linger`) el hub cierra los enlaces y cada nodo termina con `link ok` (código 0): vale que haya medido al menos un eco de `HEARTBEAT` y que el cierre lo haya hecho el hub. Si algún nodo falta, el hub termina con código 1 a los `--duration` segundos y lista los que faltan. Los nodos esperan al hub hasta 60 s (`--connect-timeout`).

**Rechazos** (desde cualquier máquina de la red, con `<IP>` del host):
```
# sin certificado de cliente: el hub cierra la conexión tras el handshake
openssl s_client -connect <IP>:7400 -tls1_3 -CAfile pki/out/ca.crt -servername host

# con certificado de otra CA
pki/make-certs.sh --out /tmp/otra-ca --nodes pi
openssl s_client -connect <IP>:7400 -tls1_3 -CAfile pki/out/ca.crt -servername host \
  -cert /tmp/otra-ca/pi.crt -key /tmp/otra-ca/pi.key
```
En ambos casos `s_client` debe terminar con una alerta (`certificate required` / `unknown ca`) o con el cierre inmediato de la conexión, y el hub no debe listar ningún par nuevo. Con `-tls1_2` el handshake debe fallar por versión.

**Solo TLS 1.3 en la red (Wireshark):**
1. Capturar en el host: `sudo tcpdump -i any -w /tmp/astrea.pcap 'tcp port 7400 or tcp port 7401'` mientras corre `connect` en otro nodo.
2. Abrir la captura en Wireshark y comprobar que el filtro `(tcp.port == 7400 || tcp.port == 7401) && !tls` no muestra tramas con datos y que `tls.handshake.type == 1` indica `Version: TLS 1.3 (0x0304)` en `supported_versions`.
3. Los datos de aplicación aparecen como `Application Data` cifrados; el nombre del nodo y las claves msgpack no son legibles.

Al terminar, se tickean M2.6 y los criterios de aceptación de `plan/milestones-and-actions.md` que dependen de las máquinas reales.
