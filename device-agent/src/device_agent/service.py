"""Device agent (Raspberry Pi): button events go to the host, the host's camera state and models come back.

- Reads the records of /dev/holo-input and sends each one to the host as `INPUT_EVENT`. The host maps buttons to
  camera actions; the agent forwards them as they are. Events produced while the link is down are dropped: the host
  stops a held movement by itself when the repeats stop arriving.
- Passes every `CAMERA_STATE` to the local renderer through its control socket.
- Assembles `MODEL_PUSH` chunks, checks them and installs `<models-dir>/<modelId>.glb` atomically, then repeats the
  current state so the renderer picks up a model that was missing when it was first asked for.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import ssl
import tempfile
from pathlib import Path

import zstandard

from astrea_common.compression import ZstdDecompress
from astrea_common.config import ClusterConfig
from astrea_common.holo_event import HOLO_EVENT_STRUCT, DecodeHoloEvents
from astrea_common.link import Connection, NodeClient
from astrea_common.messages import CameraState, Compression, InputEvent, Message, ModelAssembler, ModelIntegrityError, ModelPush, Role
from astrea_common.render_link import MODEL_ID_BYTES
from astrea_common.render_server import RendererLink

LOG = logging.getLogger("astrea.device-agent")

DEVICE_RETRY_S = 2.0
EMPTY_READ_PAUSE_S = 0.05
READ_BATCH_RECORDS = 16
MAX_MODEL_BYTES = 128 * 1024**2
SAFE_MODEL_ID = re.compile(rf"^[A-Za-z0-9_-][A-Za-z0-9_.-]{{0,{MODEL_ID_BYTES - 2}}}$")


class DeviceAgentService:
    def __init__(
        self,
        config: ClusterConfig,
        nodeName: str,
        rendererSocket: Path,
        modelsDir: Path,
        devicePath: Path | None = Path("/dev/holo-input"),
        sslContext: ssl.SSLContext | None = None,
        deviceRetryS: float = DEVICE_RETRY_S,
    ) -> None:
        self.nodeName = nodeName
        self.modelsDir = Path(modelsDir)
        self.devicePath = devicePath
        self._deviceRetryS = deviceRetryS
        self.eventsForwarded = 0
        self.eventsDropped = 0
        self.modelsInstalled = 0
        self._latestState: CameraState | None = None
        self._assemblers: dict[str, ModelAssembler] = {}
        self._stopping = False
        self._deviceTask: asyncio.Task | None = None
        self._link = RendererLink(rendererSocket, currentState=lambda: self._latestState)
        self._client = NodeClient(config.ForDeviceLink(), nodeName, Role.DEVICE_AGENT, onMessage=self._OnMessage, sslContext=sslContext)

    async def Run(self) -> None:
        self.modelsDir.mkdir(parents=True, exist_ok=True)
        await self._link.Start()
        if self.devicePath is not None:
            self._deviceTask = asyncio.create_task(self._ReadDevice(self.devicePath))
        try:
            await self._client.Run()
        finally:
            await self._ShutDown()

    async def Stop(self) -> None:
        self._stopping = True
        await self._client.Stop()

    async def WaitConnected(self, timeout: float) -> Connection:
        return await self._client.WaitConnected(timeout)

    async def _ShutDown(self) -> None:
        if self._deviceTask is not None:
            self._deviceTask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._deviceTask
        await self._link.Stop()

    async def _ReadDevice(self, path: Path) -> None:
        while not self._stopping:
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as error:
                LOG.warning("cannot open %s (%s), retrying", path, error.strerror)
                await asyncio.sleep(self._deviceRetryS)
                continue
            LOG.info("reading %s", path)
            try:
                await self._PumpEvents(descriptor)
            finally:
                os.close(descriptor)
            await asyncio.sleep(self._deviceRetryS)

    async def _PumpEvents(self, descriptor: int) -> None:
        loop = asyncio.get_running_loop()
        readable = asyncio.Event()
        loop.add_reader(descriptor, readable.set)
        leftover = b""
        try:
            while True:
                await readable.wait()
                readable.clear()
                while True:
                    try:
                        data = os.read(descriptor, READ_BATCH_RECORDS * HOLO_EVENT_STRUCT.size)
                    except BlockingIOError:
                        break
                    except OSError as error:
                        LOG.warning("lost the input device: %s", error.strerror)
                        return
                    if not data:
                        await asyncio.sleep(EMPTY_READ_PAUSE_S)
                        break
                    data = leftover + data
                    usable = len(data) - len(data) % HOLO_EVENT_STRUCT.size
                    leftover = data[usable:]
                    await self._Forward(DecodeHoloEvents(data[:usable]))
        finally:
            loop.remove_reader(descriptor)

    async def _Forward(self, events: list[InputEvent]) -> None:
        connection = self._client.connection
        for event in events:
            if connection is None or not connection.isOpen:
                self.eventsDropped += 1
                continue
            try:
                await connection.Send(event)
            except (ConnectionError, OSError):
                self.eventsDropped += 1
            else:
                self.eventsForwarded += 1

    async def _OnMessage(self, connection: Connection, message: Message) -> None:
        if isinstance(message, CameraState):
            self._latestState = message
            self._link.Send(message)
        elif isinstance(message, ModelPush):
            await self._OnModelChunk(message)

    async def _OnModelChunk(self, chunk: ModelPush) -> None:
        if not SAFE_MODEL_ID.match(chunk.modelId) or not chunk.modelId.isascii():
            LOG.warning("ignoring a model with the unusable id %r", chunk.modelId)
            return
        if chunk.totalSize > MAX_MODEL_BYTES:
            LOG.warning("ignoring '%s': %d bytes exceed the %d byte limit", chunk.modelId, chunk.totalSize, MAX_MODEL_BYTES)
            return
        data = self._AddChunk(chunk)
        if data is None:
            return
        try:
            self._Install(chunk, data)
        except (OSError, ValueError, ModelIntegrityError, zstandard.ZstdError) as error:
            LOG.warning("could not install '%s': %s", chunk.modelId, error)
            return
        self.modelsInstalled += 1
        LOG.info("installed model '%s' (%d bytes)", chunk.modelId, len(data))
        if self._latestState is not None and self._latestState.modelId == chunk.modelId:
            self._link.Send(self._latestState)

    def _AddChunk(self, chunk: ModelPush) -> bytes | None:
        assembler = self._assemblers.setdefault(chunk.modelId, ModelAssembler())
        try:
            data = assembler.Add(chunk)
        except ModelIntegrityError as error:
            LOG.warning("restarting the transfer of '%s': %s", chunk.modelId, error)
            assembler = self._assemblers[chunk.modelId] = ModelAssembler()
            try:
                data = assembler.Add(chunk)
            except ModelIntegrityError:
                del self._assemblers[chunk.modelId]
                return None
        if data is not None:
            del self._assemblers[chunk.modelId]
        return data

    def _Install(self, chunk: ModelPush, data: bytes) -> None:
        model = ZstdDecompress(data, MAX_MODEL_BYTES) if chunk.compression is Compression.ZSTD else data
        self.modelsDir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.modelsDir, prefix=f".{chunk.modelId}-", suffix=".part", delete=False) as handle:
            handle.write(model)
            partial = Path(handle.name)
        os.replace(partial, self.modelsDir / f"{chunk.modelId}.glb")
