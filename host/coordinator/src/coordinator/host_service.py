"""astrea-host: the permanent host service that keeps the hologram and the host viewer on the same camera.

It owns the camera state (`CameraControl`) and merges the keyboard and mouse of the host viewer, reported over the
renderer's Unix socket, with the button events of the Pi's device agent, received as `INPUT_EVENT`. Every new state
goes to both viewers, and models in --models-dir are pushed to the Pi each time its agent connects.

It listens on its own port (`hub.device-port`, by default `hub.port + 1`), so it runs next to
`astrea-coordinator reconstruct`, which keeps the reconstruction hub on `hub.port`.

    uv run astrea-host --models-dir out/models
    renderer/build/renderer --mode viewer --up-axis z --model out/models/cup.glb \\
        --control-socket /tmp/astrea-viewer.sock --models-dir out/models

The ids are the file names without .glb and are read when the service starts, so a new model needs a restart. The
host viewer can use its own --models-dir (for example one with the high-LOD files under the same ids), because only
the folder of astrea-host is pushed to the Pi.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

from astrea_common.config import ClusterConfig, ConfigError, LoadConfig
from astrea_common.holo_event import DescribeEvent, EventValue
from astrea_common.link import Connection, HubServer
from astrea_common.messages import CameraState, Compression, InputEvent, Message, Role, SplitModel
from astrea_common.render_link import MODEL_ID_BYTES
from coordinator.camera_control import CameraControl, InputDevice
from coordinator.renderer_bridge import RendererBridge

LOG = logging.getLogger("astrea.host")

MODEL_ID_PATTERN = re.compile(rf"^[A-Za-z0-9_-][A-Za-z0-9_.-]{{0,{MODEL_ID_BYTES - 2}}}$")


def FindModelIds(modelsDir: Path) -> tuple[str, ...]:
    """One model per `<id>.glb`, in name order. Files whose name is not a usable id are skipped."""
    if not modelsDir.is_dir():
        return ()
    return tuple(path.stem for path in sorted(modelsDir.glob("*.glb")) if MODEL_ID_PATTERN.match(path.stem))


class HostService:
    def __init__(
        self,
        config: ClusterConfig,
        modelsDir: Path,
        rendererSocket: Path,
        modelIds: tuple[str, ...] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.modelsDir = Path(modelsDir)
        self.modelsPushed = 0
        self.eventsReceived = 0
        self._clock = clock
        self.control = CameraControl(modelIds if modelIds is not None else FindModelIds(self.modelsDir), startS=clock())
        self.bridge = RendererBridge(self.control, rendererSocket, clock=clock)
        self.bridge.AddStateListener(self._QueueState)
        self.hub = HubServer(
            config,
            onMessage=self._OnMessage,
            onPeerConnected=self._OnPeerConnected,
            onPeerLost=self._OnPeerLost,
            link="device",
        )
        self._latestToSend: CameraState | None = None
        self._stateReady = asyncio.Event()
        self._senderTask: asyncio.Task | None = None
        self._syncTasks: set[asyncio.Task] = set()

    async def Start(self) -> None:
        await self.bridge.Start()
        await self.hub.Start()
        self._senderTask = asyncio.create_task(self._SendStates())

    async def Stop(self) -> None:
        if self._senderTask is not None:
            self._senderTask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._senderTask
        for task in list(self._syncTasks):
            task.cancel()
        await self.hub.Stop()
        await self.bridge.Stop()

    def _QueueState(self, state: CameraState) -> None:
        self._latestToSend = state
        self._stateReady.set()

    async def _SendStates(self) -> None:
        """Sends only the newest state: a slow link skips the ones in between instead of falling behind."""
        while True:
            await self._stateReady.wait()
            self._stateReady.clear()
            state = self._latestToSend
            if state is not None:
                await self._SendToAgents(state)

    def _Agents(self) -> list[Connection]:
        return [connection for (_, role), connection in self.hub.peers.items() if role is Role.DEVICE_AGENT]

    async def _SendToAgents(self, message: Message) -> None:
        for connection in self._Agents():
            try:
                await connection.Send(message)
            except (ConnectionError, OSError) as error:
                LOG.warning("could not reach %s: %s", connection.peer.node, error)

    async def _OnMessage(self, connection: Connection, message: Message) -> None:
        if isinstance(message, InputEvent) and connection.peer.role is Role.DEVICE_AGENT:
            self.eventsReceived += 1
            handled = self.control.HandleButtonEvent(message, self._clock())
            if message.value != EventValue.REPEAT:
                LOG.info("%s from %s: %s", "button" if handled else "ignored button event", connection.peer.node, DescribeEvent(message))

    async def _OnPeerConnected(self, connection: Connection) -> None:
        if connection.peer.role is not Role.DEVICE_AGENT:
            return
        task = asyncio.create_task(self._SyncAgent(connection))
        self._syncTasks.add(task)
        task.add_done_callback(self._syncTasks.discard)

    async def _OnPeerLost(self, connection: Connection, reason: str) -> None:
        if connection.peer.role is Role.DEVICE_AGENT and not self._Agents():
            self.control.ReleaseDevice(InputDevice.BUTTONS, self._clock())

    async def _SyncAgent(self, connection: Connection) -> None:
        """What a freshly connected agent needs: the current view, then every model, then the view again."""
        try:
            await connection.Send(self.control.Snapshot())
            for modelId in FindModelIds(self.modelsDir):
                await self._PushModel(connection, modelId)
            await connection.Send(self.control.Snapshot())
        except (ConnectionError, OSError) as error:
            LOG.warning("could not sync %s: %s", connection.peer.node, error)

    async def _PushModel(self, connection: Connection, modelId: str) -> None:
        data = await asyncio.to_thread((self.modelsDir / f"{modelId}.glb").read_bytes)
        for chunk in SplitModel(modelId, data, Compression.NONE):
            await connection.Send(chunk)
        self.modelsPushed += 1
        LOG.info("pushed model '%s' to %s (%d bytes)", modelId, connection.peer.node, len(data))


async def Serve(service: HostService) -> None:
    await service.Start()
    try:
        await asyncio.Event().wait()
    finally:
        await service.Stop()


def ParseArgs(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="astrea-host", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.toml", help="cluster configuration (default: config.toml)")
    parser.add_argument("--models-dir", default="out/models", help="folder with one <id>.glb per model (LOD-low for the Pi); read at startup and pushed to the Pi when its agent connects")
    parser.add_argument("--renderer-socket", default="/tmp/astrea-viewer.sock", help="Unix socket the host viewer connects to")
    parser.add_argument("--models", default=None, help="comma separated model ids that N cycles through (default: the files in --models-dir)")
    parser.add_argument("--verbose", action="store_true", help="log link, model and state events")
    return parser.parse_args(argv)


def Main(argv: list[str] | None = None) -> int:
    args = ParseArgs(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    try:
        config = LoadConfig(args.config)
        ids = None if args.models is None else tuple(name for name in args.models.split(",") if name)
        service = HostService(config, Path(args.models_dir), Path(args.renderer_socket), ids)
        listed = ids if ids is not None else FindModelIds(service.modelsDir)
        print(
            f"astrea-host listening on {config.hub.bindAddress}:{config.hub.devicePort}; viewer socket {args.renderer_socket}; "
            f"models: {', '.join(listed) or 'none'}",
            flush=True,
        )
        asyncio.run(Serve(service))
    except ConfigError as error:
        print(f"astrea-host: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(Main())
