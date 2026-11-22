# Protocolo de red de AstreaView

Especificación del transporte seguro y de los mensajes entre los nodos (M2). La implementación está en `common/src/astrea_common/` (`framing.py`, `messages.py`, `tls.py`, `config.py`, `link.py`) y la verificación en `astrea-net-check`.

## 1. Topología

El host PC es el **hub**: escucha en un puerto TCP fijo (`hub.port`, por defecto 7400). Worker B y la Pi **abren la conexión hacia el hub**; no hay enlaces entre ellos. Cada conexión es bidireccional y persistente, y cada nodo se reconecta con espera exponencial (`reconnect-initial-s` a `reconnect-max-s`).

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
- **Cifrado:** los tres suites TLS 1.3 son AEAD. Python no permite elegir suites de TLS 1.3; para que un proceso use solo ChaCha20-Poly1305 (la Pi 3 no tiene aceleración AES) se lanza con `OPENSSL_CONF=pki/openssl-chacha20.cnf`.
- El socket Unix entre el proceso Python local y el renderer no sale de la máquina y no usa TLS.

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
| `JOB_RESULT` | 3 | `jobId` str, `part`/`parts` int, `status` (`ok` / `error`), `error` str, `elapsedS`, `viewIndices[]`, `blocks[]` |
| `HEARTBEAT` | 4 | `sequence` int, `sentAtNs` int, `echo` bool |
| `INPUT_EVENT` | 5 | `source`, `code`, `value`, `timestampNs` (espejo de `struct HoloEvent`; `source`: 1 joystick, 2 gesto ZX) |
| `CAMERA_STATE` | 6 | `yaw`, `pitch`, `zoom` float, `modelId` str |
| `MODEL_PUSH` | 7 | `modelId`, `index`, `count`, `totalSize`, `sha256` (hex del modelo completo tal como se transmite), `compression` (`none` / `zstd`), `data` bin |
| `ROTATE` | 8 | `requestId`, `steps` (definido, deshabilitado) |
| `ROTATE_DONE` | 9 | `requestId`, `positionSteps` (definido, deshabilitado) |

Sub-estructuras:
- **`views[]`** (de `JOB_ASSIGN`): `index`, `intrinsics {fx, fy, cx, cy, width, height}`, `rotation[9]` (fila mayor) y `translationMm[3]` con `xCamera = rotation · xPlate + translationMm`, `rgb` y `depthM` como `ArrayBlob`.
- **`blocks[]`** (de `JOB_RESULT`): `startIndex[3]` y `shape[3]` de una caja de vóxeles con peso > 0, y `tsdf`, `weight`, `color` como `ArrayBlob`.
- **`ArrayBlob`**: `dtype` (enteros o flotantes de hasta 8 bytes, little endian), `shape` (1 a 4 dimensiones), `compression` (`none` / `zstd`), `data`. La descompresión está acotada por el tamaño que declara `shape`.
- **Modelos:** el receptor junta los chunks en cualquier orden y acepta el modelo solo si el tamaño y el SHA-256 coinciden (`ModelAssembler`).

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
3. En `config.toml`: `hub.address = <IP del host>` y `hub.bind-address = "0.0.0.0"`. Abrir el puerto 7400/TCP en el host.
4. En la Pi y en worker B: `git clone` del repositorio y `uv sync`.

**Enlace de los tres nodos:**
```
# host
uv run astrea-net-check serve --expect worker-b pi:device-agent pi:worker --duration 120

# worker B (sin --duration: corre hasta que el hub cierre el enlace; se pueden lanzar en cualquier orden y con minutos de diferencia)
uv run astrea-net-check connect --name worker-b

# Pi, forzando ChaCha20-Poly1305 (un proceso por rol)
OPENSSL_CONF=pki/openssl-chacha20.cnf uv run astrea-net-check connect --name pi --role device-agent
OPENSSL_CONF=pki/openssl-chacha20.cnf uv run astrea-net-check connect --name pi --role worker
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
1. Capturar en el host: `sudo tcpdump -i any -w /tmp/astrea.pcap 'tcp port 7400'` mientras corre `connect` en otro nodo.
2. Abrir la captura en Wireshark y comprobar que el filtro `tcp.port == 7400 && !tls` no muestra tramas con datos y que `tls.handshake.type == 1` indica `Version: TLS 1.3 (0x0304)` en `supported_versions`.
3. Los datos de aplicación aparecen como `Application Data` cifrados; el nombre del nodo y las claves msgpack no son legibles.

Al terminar, se tickean M2.6 y los criterios de aceptación de `plan/milestones-and-actions.md` que dependen de las máquinas reales.
