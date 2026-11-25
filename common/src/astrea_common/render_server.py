"""Server end of the renderer's Unix socket (see `renderer/src/control-protocol.h`).

The renderer connects as a client. One renderer at a time: a new connection replaces the previous one, and a
newly connected renderer is sent the current state right away. The host bridge and the Pi's device agent use it.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path

from astrea_common.messages import CameraState
from astrea_common.render_link import EncodeState, InputDecoder, RendererInput

LOGGER = logging.getLogger(__name__)

MAX_UNSENT_BYTES = 4096


class RendererLink:
    def __init__(
        self,
        socketPath: Path,
        onInput: Callable[[RendererInput], None] | None = None,
        onDisconnected: Callable[[], None] | None = None,
        currentState: Callable[[], CameraState | None] | None = None,
    ) -> None:
        self._socketPath = Path(socketPath)
        self._onInput = onInput
        self._onDisconnected = onDisconnected
        self._currentState = currentState
        self._server: asyncio.AbstractServer | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.transport.is_closing()

    async def Start(self) -> None:
        self._socketPath.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._Serve, path=str(self._socketPath))

    async def Stop(self) -> None:
        self._CloseRenderer()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._socketPath.unlink(missing_ok=True)

    def Send(self, state: CameraState) -> None:
        """Latest state wins: nothing is queued behind a renderer that is not keeping up."""
        writer = self._writer
        if writer is None or writer.transport.is_closing():
            return
        if writer.transport.get_write_buffer_size() > MAX_UNSENT_BYTES:
            return
        writer.write(EncodeState(state))

    def _CloseRenderer(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    async def _Serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._CloseRenderer()
        self._writer = writer
        state = self._currentState() if self._currentState is not None else None
        if state is not None:
            self.Send(state)
        decoder = InputDecoder()
        try:
            while data := await reader.read(256):
                for event in decoder.Feed(data):
                    if self._onInput is not None:
                        self._onInput(event)
        except (ConnectionError, ValueError) as error:
            LOGGER.warning("renderer link dropped: %s", error)
        finally:
            if self._writer is writer:
                self._writer = None
                if self._onDisconnected is not None:
                    self._onDisconnected()
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
