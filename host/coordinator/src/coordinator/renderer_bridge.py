"""Host side of the renderer's Unix socket: feeds its raw input into CameraControl and sends back the camera state.

The renderer never moves the camera by itself while it is connected; every state it draws comes from here, the same
state that is sent to the Pi, so both viewers show the same view.

Manual test with a viewer window (models are looked up as <models-dir>/<modelId>.glb):

    uv run python -m coordinator.renderer_bridge --socket viewer.sock --models cup,bunny
    renderer/build/renderer --mode viewer --up-axis z --model out/scans/cup/model/lod-low.glb \\
        --control-socket viewer.sock --models-dir out/scans/cup/model
"""

import argparse
import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path

from astrea_common.messages import CameraState
from astrea_common.render_link import InputAction, InputKind, RendererInput
from astrea_common.render_server import RendererLink
from coordinator.camera_control import CameraAction, CameraControl, InputDevice

LOGGER = logging.getLogger(__name__)

TICK_INTERVAL_S = 1.0 / 60.0


class RendererBridge:
    def __init__(
        self,
        control: CameraControl,
        socketPath: Path,
        clock: Callable[[], float] = time.monotonic,
        tickIntervalS: float = TICK_INTERVAL_S,
    ) -> None:
        self._control = control
        self._clock = clock
        self._tickIntervalS = tickIntervalS
        self._ticker: asyncio.Task | None = None
        self._stateListeners: list[Callable[[CameraState], None]] = []
        self._link = RendererLink(
            socketPath,
            onInput=self._Dispatch,
            onDisconnected=lambda: self._control.ReleaseDevice(InputDevice.KEYBOARD, self._clock()),
            currentState=self._control.Snapshot,
        )

    def AddStateListener(self, listener: Callable[[CameraState], None]) -> None:
        self._stateListeners.append(listener)

    async def Start(self) -> None:
        await self._link.Start()
        self._ticker = asyncio.create_task(self._Tick())

    async def Stop(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
        await self._link.Stop()

    async def _Tick(self) -> None:
        while True:
            state = self._control.PollEmission(self._clock())
            if state is not None:
                self._link.Send(state)
                for listener in self._stateListeners:
                    listener(state)
            await asyncio.sleep(self._tickIntervalS)

    def _Dispatch(self, event: RendererInput) -> None:
        nowS = self._clock()
        if event.kind == InputKind.MOUSE_DELTA:
            self._control.ApplyMouseDelta(event.value, nowS)
            return
        try:
            action = CameraAction[InputAction(event.action).name]
        except ValueError:
            LOGGER.warning("renderer sent an unknown action %d", event.action)
            return
        if event.kind == InputKind.ACTION_PRESS:
            self._control.PressKey(action, nowS)
        else:
            self._control.ReleaseKey(action, nowS)


async def _Serve(arguments: argparse.Namespace) -> None:
    control = CameraControl(tuple(name for name in arguments.models.split(",") if name), startS=time.monotonic())
    bridge = RendererBridge(control, arguments.socket)
    bridge.AddStateListener(lambda state: LOGGER.info("state %s", state))
    await bridge.Start()
    try:
        await asyncio.Event().wait()
    finally:
        await bridge.Stop()


def Main() -> int:
    parser = argparse.ArgumentParser(description="Serve the camera state to a renderer started with --control-socket.")
    parser.add_argument("--socket", type=Path, required=True, help="Unix socket path the renderer connects to")
    parser.add_argument("--models", default="", help="comma separated model ids that N cycles through")
    parser.add_argument("--verbose", action="store_true", help="log every state sent")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO if arguments.verbose else logging.WARNING)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_Serve(arguments))
    return 0


if __name__ == "__main__":
    raise SystemExit(Main())
