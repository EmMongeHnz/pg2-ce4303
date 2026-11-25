"""Manages an `astrea-netd` process, the C daemon that does all the TLS 1.3 networking of a node.

Python never opens a network socket: it starts the daemon, connects to its Unix socket and exchanges whole messages
with it using the local protocol described in `net/src/astrea-netd.c` (`[u32 length][u8 kind][body]`, big-endian).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from astrea_common.config import ClusterConfig

LOG = logging.getLogger("astrea.net")

REPO_ROOT = Path(__file__).resolve().parents[3]
NET_DIRECTORY = REPO_ROOT / "net"
DAEMON_ENVIRONMENT_VARIABLE = "ASTREA_NETD"
START_TIMEOUT_S = 8.0
STOP_TIMEOUT_S = 3.0
LOCAL_MAX_FRAME = 4 * 1024 * 1024 + 64

EV_PEER_UP = 1
EV_PEER_DOWN = 2
EV_MESSAGE = 3
EV_RTT = 4
EV_REJECT = 5
EV_FATAL = 6
EV_DETACHED = 100

CMD_SEND = 1
CMD_CLOSE = 2
CMD_SHUTDOWN = 3

_LENGTH = struct.Struct(">I")

EventHandler = Callable[[int, bytes], Awaitable[None]]


class DaemonError(RuntimeError):
    pass


def FindProgram(name: str, environmentVariable: str | None = None) -> Path:
    """A program built from net/: the path in the environment variable, or net/build/<name> (built with `make -C net`
    when it is missing)."""
    configured = os.environ.get(environmentVariable) if environmentVariable else None
    if configured:
        return Path(configured)
    binary = NET_DIRECTORY / "build" / name
    if not binary.exists() and shutil.which("make"):
        subprocess.run(["make", "-C", str(NET_DIRECTORY)], check=False, capture_output=True)
    if not binary.exists():
        raise DaemonError(f"{binary} does not exist; build it with `make -C net` (needs build-essential and libssl-dev)")
    return binary


def FindDaemon() -> Path:
    return FindProgram("astrea-netd", DAEMON_ENVIRONMENT_VARIABLE)


class DaemonSession:
    def __init__(self, config: ClusterConfig, arguments: list[str], onEvent: EventHandler) -> None:
        self._config = config
        self._arguments = arguments
        self._onEvent = onEvent
        self._directory: str | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pump: asyncio.Task | None = None
        self._sendLock = asyncio.Lock()
        self._stopped = False

    @property
    def isRunning(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def Start(self) -> None:
        self._directory = tempfile.mkdtemp(prefix="astrea-net-")
        configPath = Path(self._directory) / "config.toml"
        configPath.write_text(self._config.ToToml())
        socketPath = Path(self._directory) / "net.sock"
        command = [str(FindDaemon()), "--config", str(configPath), "--socket", str(socketPath), "--exit-on-detach", *self._arguments]
        if LOG.isEnabledFor(logging.INFO):
            command.append("--verbose")
        self._process = await asyncio.create_subprocess_exec(*command)
        try:
            self._reader, self._writer = await self._Connect(socketPath)
        except BaseException:
            await self.Stop()
            raise
        self._pump = asyncio.create_task(self._Pump())

    async def _Connect(self, socketPath: Path):
        assert self._process is not None
        deadline = asyncio.get_running_loop().time() + START_TIMEOUT_S
        while True:
            if self._process.returncode is not None:
                raise DaemonError(f"astrea-netd exited with code {self._process.returncode} (its message is above)")
            try:
                return await asyncio.open_unix_connection(str(socketPath))
            except (FileNotFoundError, ConnectionRefusedError):
                if asyncio.get_running_loop().time() > deadline:
                    raise DaemonError("astrea-netd did not start in time") from None
                await asyncio.sleep(0.02)

    async def _Pump(self) -> None:
        assert self._reader is not None
        try:
            while True:
                header = await self._reader.readexactly(4)
                (length,) = _LENGTH.unpack(header)
                if length == 0 or length > LOCAL_MAX_FRAME:
                    raise DaemonError(f"the daemon sent a frame of {length} bytes")
                body = await self._reader.readexactly(length)
                await self._onEvent(body[0], body[1:])
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("handling an event of the network daemon failed")
        if not self._stopped:
            await self._onEvent(EV_DETACHED, b"")

    async def Command(self, kind: int, body: bytes = b"") -> None:
        if not self.isRunning:
            raise ConnectionError("the network daemon is not running")
        assert self._writer is not None
        async with self._sendLock:
            self._writer.write(_LENGTH.pack(len(body) + 1) + bytes([kind]) + body)
            await self._writer.drain()

    async def Stop(self) -> None:
        self._stopped = True
        if self._writer is not None:
            with contextlib.suppress(ConnectionError, OSError):
                self._writer.write(_LENGTH.pack(1) + bytes([CMD_SHUTDOWN]))
                await self._writer.drain()
            self._writer.close()
        if self._pump is not None and self._pump is not asyncio.current_task():
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
        if self._process is not None and self._process.returncode is None:
            try:
                await asyncio.wait_for(self._process.wait(), STOP_TIMEOUT_S)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None
