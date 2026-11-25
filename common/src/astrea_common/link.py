"""Secure links between the hub (host PC) and the other nodes, as seen from Python.

The TLS 1.3 handshake with mutual authentication, the framing, the `HELLO` exchange, the message rules, the
heartbeats and the reconnection all run in the C daemon `astrea-netd` (see `net/`); this module only starts it and
exchanges whole messages with it. `HubServer` and `NodeClient` keep the interface the workers, the coordinator and
the host service were written against, and `Connection` is one established link.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass
from typing import Awaitable, Callable

import msgpack

from astrea_common.config import ClusterConfig
from astrea_common.framing import MAX_FRAME_BYTES, Frame, FrameTooLarge, ProtocolError
from astrea_common.messages import DecodeMessage, IsAllowed, Message, MessageType, Role
from astrea_common.net_daemon import (
    CMD_CLOSE,
    CMD_SEND,
    EV_DETACHED,
    EV_FATAL,
    EV_MESSAGE,
    EV_PEER_DOWN,
    EV_PEER_UP,
    EV_REJECT,
    EV_RTT,
    DaemonSession,
)

LOG = logging.getLogger("astrea.link")

MessageHandler = Callable[["Connection", Message], Awaitable[None]]
PeerHandler = Callable[["Connection"], Awaitable[None]]
LostHandler = Callable[["Connection", str], Awaitable[None]]

_ID = struct.Struct(">I")


class HandshakeRejected(ProtocolError):
    pass


class MessageNotAllowed(ProtocolError):
    pass


@dataclass(frozen=True)
class PeerInfo:
    node: str
    role: Role
    session: str


class Connection:
    """An established link, from either side. Created from the daemon's `PEER_UP` event."""

    def __init__(self, session: DaemonSession, peerId: int, peer: PeerInfo, localIsHub: bool, nodeRole: Role) -> None:
        self.peer = peer
        self.rttMs: float | None = None
        self.closeReason: str | None = None
        self._session = session
        self._peerId = peerId
        self._localIsHub = localIsHub
        self._nodeRole = nodeRole
        self._closed = asyncio.Event()

    @property
    def isOpen(self) -> bool:
        return not self._closed.is_set()

    def Start(self) -> None:
        """Kept for the callers that start a link explicitly; the daemon already runs it."""

    async def Wait(self) -> None:
        await self._closed.wait()

    async def Send(self, message: Message) -> None:
        messageType = message.MESSAGE_TYPE
        if messageType in (MessageType.HELLO, MessageType.HEARTBEAT):
            raise MessageNotAllowed(f"{messageType.name} is managed by the link itself")
        if not IsAllowed(messageType, self._localIsHub, self._nodeRole):
            raise MessageNotAllowed(f"{messageType.name} cannot be sent to {self.peer.node} ({self.peer.role.value})")
        if not self.isOpen:
            raise ConnectionError(f"the link to {self.peer.node} is closed")
        payload = msgpack.packb(message.ToPayload(), use_bin_type=True)
        if len(payload) + 1 > MAX_FRAME_BYTES:
            raise FrameTooLarge(f"a frame of {len(payload) + 1} bytes exceeds the {MAX_FRAME_BYTES} byte limit")
        await self._session.Command(CMD_SEND, _ID.pack(self._peerId) + bytes([int(messageType)]) + payload)

    async def Close(self, reason: str = "closed locally") -> None:
        if not self.isOpen:
            return
        self.MarkClosed(reason)
        try:
            await self._session.Command(CMD_CLOSE, _ID.pack(self._peerId) + reason.encode())
        except ConnectionError:
            pass

    def MarkClosed(self, reason: str) -> None:
        if self._closed.is_set():
            return
        self.closeReason = reason
        self._closed.set()


def _ParsePeerUp(body: bytes) -> tuple[int, PeerInfo]:
    (peerId,) = _ID.unpack_from(body, 0)
    position = 4
    nodeLength = body[position]
    node = body[position + 1 : position + 1 + nodeLength].decode()
    position += 1 + nodeLength
    roleLength = body[position]
    role = Role(body[position + 1 : position + 1 + roleLength].decode())
    position += 1 + roleLength
    (sessionLength,) = struct.unpack_from(">H", body, position)
    session = body[position + 2 : position + 2 + sessionLength].decode()
    return peerId, PeerInfo(node, role, session)


async def _DeliverMessage(connection: Connection, body: bytes, onMessage: MessageHandler | None) -> None:
    messageType = body[4]
    try:
        message = DecodeMessage(Frame(messageType, msgpack.unpackb(body[5:], raw=False)))
    except (ProtocolError, ValueError, msgpack.exceptions.ExtraData, msgpack.exceptions.UnpackException) as error:
        LOG.warning("closing %s: %s", connection.peer.node, error)
        await connection.Close(f"protocol error: {error}")
        return
    if onMessage is None:
        return
    try:
        await onMessage(connection, message)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        LOG.exception("handler for %s failed", connection.peer.node)
        await connection.Close(f"handler failed: {error}")


class HubServer:
    """Runs the hub side in a daemon, keeps one live connection per (node, role) and reports arrivals and losses.

    `link` picks the port: "reconstruction" listens on `hub.port` (the workers) and "device" on `hub.device-port`
    (the device agent), so both services can run at once.
    """

    def __init__(
        self,
        config: ClusterConfig,
        onMessage: MessageHandler | None = None,
        onPeerConnected: PeerHandler | None = None,
        onPeerLost: LostHandler | None = None,
        link: str = "reconstruction",
    ) -> None:
        self.config = config
        self.rejections: list[str] = []
        self._onMessage = onMessage
        self._onPeerConnected = onPeerConnected
        self._onPeerLost = onPeerLost
        self._session = DaemonSession(config, ["--mode", "hub", "--link", link], self._OnEvent)
        self._peers: dict[tuple[str, Role], Connection] = {}
        self._byId: dict[int, Connection] = {}

    @property
    def peers(self) -> dict[tuple[str, Role], Connection]:
        return dict(self._peers)

    async def Start(self) -> None:
        await self._session.Start()

    async def Stop(self) -> None:
        await self._session.Stop()
        for connection in list(self._byId.values()):
            connection.MarkClosed("hub stopped")
        self._peers.clear()
        self._byId.clear()

    async def Send(self, node: str, role: Role, message: Message) -> None:
        connection = self._peers.get((node, role))
        if connection is None or not connection.isOpen:
            raise ConnectionError(f"{node} ({role.value}) is not connected")
        await connection.Send(message)

    async def _OnEvent(self, kind: int, body: bytes) -> None:
        if kind == EV_PEER_UP:
            await self._PeerUp(body)
        elif kind == EV_PEER_DOWN:
            await self._PeerDown(body)
        elif kind == EV_MESSAGE:
            connection = self._byId.get(_ID.unpack_from(body, 0)[0])
            if connection is not None and connection.isOpen:
                await _DeliverMessage(connection, body, self._onMessage)
        elif kind == EV_RTT:
            connection = self._byId.get(_ID.unpack_from(body, 0)[0])
            if connection is not None:
                connection.rttMs = _ID.unpack_from(body, 4)[0] / 1000.0
        elif kind == EV_REJECT:
            reason = body.decode(errors="replace")
            LOG.warning("rejected connection: %s", reason)
            self.rejections.append(reason)
        elif kind == EV_FATAL:
            LOG.error("network daemon: %s", body.decode(errors="replace"))
        elif kind == EV_DETACHED:
            LOG.error("the network daemon of the hub stopped")

    async def _PeerUp(self, body: bytes) -> None:
        peerId, peer = _ParsePeerUp(body)
        connection = Connection(self._session, peerId, peer, True, peer.role)
        key = (peer.node, peer.role)
        previous = self._peers.get(key)
        self._peers[key] = connection
        self._byId[peerId] = connection
        if previous is not None:
            previous.MarkClosed("replaced by a newer connection")
        LOG.info("%s (%s) connected over %s", peer.node, peer.role.value, peer.session)
        if self._onPeerConnected is not None:
            await self._onPeerConnected(connection)

    async def _PeerDown(self, body: bytes) -> None:
        peerId = _ID.unpack_from(body, 0)[0]
        reason = body[4:].decode(errors="replace")
        connection = self._byId.pop(peerId, None)
        if connection is None:
            return
        connection.MarkClosed(reason)
        key = (connection.peer.node, connection.peer.role)
        if self._peers.get(key) is connection:
            del self._peers[key]
        LOG.info("%s (%s) lost: %s", connection.peer.node, connection.peer.role.value, connection.closeReason)
        if self._onPeerLost is not None:
            await self._onPeerLost(connection, connection.closeReason or "closed")


class NodeClient:
    """Keeps a link to the hub alive: the daemon dials, waits until it drops and retries with exponential backoff."""

    def __init__(
        self,
        config: ClusterConfig,
        nodeName: str,
        role: Role,
        onMessage: MessageHandler | None = None,
        onConnected: PeerHandler | None = None,
        onDisconnected: LostHandler | None = None,
        preferChacha: bool = False,
    ) -> None:
        self.config = config
        self.nodeName = nodeName
        self.role = role
        self.connection: Connection | None = None
        self._onMessage = onMessage
        self._onConnected = onConnected
        self._onDisconnected = onDisconnected
        arguments = ["--mode", "node", "--name", nodeName, "--role", role.value]
        if preferChacha:
            arguments.append("--prefer-chacha20")
        self._session = DaemonSession(config, arguments, self._OnEvent)
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._byId: dict[int, Connection] = {}

    async def WaitConnected(self, timeout: float) -> Connection:
        await asyncio.wait_for(self._connected.wait(), timeout)
        assert self.connection is not None
        return self.connection

    async def Stop(self) -> None:
        self._stop.set()

    async def Run(self) -> None:
        await self._session.Start()
        try:
            await self._stop.wait()
        finally:
            if self.connection is not None:
                self.connection.MarkClosed("node stopped")
            await self._session.Stop()
            self._connected.clear()
            self.connection = None

    async def _OnEvent(self, kind: int, body: bytes) -> None:
        if kind == EV_PEER_UP:
            peerId, peer = _ParsePeerUp(body)
            connection = Connection(self._session, peerId, peer, False, self.role)
            self._byId[peerId] = connection
            self.connection = connection
            self._connected.set()
            if self._onConnected is not None:
                await self._onConnected(connection)
        elif kind == EV_PEER_DOWN:
            connection = self._byId.pop(_ID.unpack_from(body, 0)[0], None)
            if connection is None:
                return
            connection.MarkClosed(body[4:].decode(errors="replace"))
            if self.connection is connection:
                self.connection = None
                self._connected.clear()
            if self._onDisconnected is not None:
                await self._onDisconnected(connection, connection.closeReason or "closed")
        elif kind == EV_MESSAGE:
            connection = self._byId.get(_ID.unpack_from(body, 0)[0])
            if connection is not None and connection.isOpen:
                await _DeliverMessage(connection, body, self._onMessage)
        elif kind == EV_RTT:
            connection = self._byId.get(_ID.unpack_from(body, 0)[0])
            if connection is not None:
                connection.rttMs = _ID.unpack_from(body, 4)[0] / 1000.0
        elif kind == EV_DETACHED and not self._stop.is_set():
            LOG.error("the network daemon of %s stopped", self.nodeName)
            self._stop.set()
