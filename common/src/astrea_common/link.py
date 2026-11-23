"""Secure links between the hub (host PC) and the other nodes.

`HubServer` accepts mutual-TLS connections and `NodeClient` dials the hub with reconnection. Both ends run
the same `Connection`: a `HELLO` exchange that binds the declared node and role to the certificate and to
`config.toml`, then a receive loop that enforces the direction rules of `astrea_common.messages`, a
periodic `HEARTBEAT` (the peer echoes it, which gives the round-trip time) and a watchdog that closes
the link when no bytes arrived for `heartbeat-timeout-s`. Liveness counts every received chunk, not only
complete frames, so a large frame on a slow link does not look like a dead peer.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from astrea_common.config import ClusterConfig, NetworkSettings
from astrea_common.framing import Frame, FrameDecoder, ProtocolError, ReadFrame, WriteFrame
from astrea_common.messages import (
    PROTOCOL_VERSION,
    Heartbeat,
    Hello,
    IsAllowed,
    Message,
    MessageType,
    Role,
    DecodeMessage,
)
from astrea_common.tls import ClientContextFor, DescribeSession, PeerIdentityError, PeerNodeName, ServerContextFor

LOG = logging.getLogger("astrea.link")
WATCHDOG_CHECKS_PER_TIMEOUT = 4
RECEIVE_CHUNK_BYTES = 64 * 1024
NS_PER_MS = 1_000_000

MessageHandler = Callable[["Connection", Message], Awaitable[None]]
PeerHandler = Callable[["Connection"], Awaitable[None]]
LostHandler = Callable[["Connection", str], Awaitable[None]]


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
    """An established link, from either side. Create it with `AcceptPeer` (hub) or `DialHub` (node)."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer: PeerInfo,
        localIsHub: bool,
        nodeRole: Role,
        settings: NetworkSettings,
        onMessage: MessageHandler | None,
    ) -> None:
        self.peer = peer
        self.rttMs: float | None = None
        self.closeReason: str | None = None
        self._reader = reader
        self._writer = writer
        self._localIsHub = localIsHub
        self._nodeRole = nodeRole
        self._settings = settings
        self._onMessage = onMessage
        self._closed = asyncio.Event()
        self._sendLock = asyncio.Lock()
        self._lastHeardNs = time.monotonic_ns()
        self._sequence = 0
        self._tasks: list[asyncio.Task] = []

    @property
    def isOpen(self) -> bool:
        return not self._closed.is_set()

    def Start(self) -> None:
        self._tasks = [asyncio.create_task(loop()) for loop in (self._ReceiveLoop, self._HeartbeatLoop, self._WatchdogLoop)]

    async def Wait(self) -> None:
        await self._closed.wait()

    async def Send(self, message: Message) -> None:
        messageType = message.MESSAGE_TYPE
        if messageType in (MessageType.HELLO, MessageType.HEARTBEAT):
            raise MessageNotAllowed(f"{messageType.name} is managed by the link itself")
        if not IsAllowed(messageType, self._localIsHub, self._nodeRole):
            raise MessageNotAllowed(f"{messageType.name} cannot be sent to {self.peer.node} ({self.peer.role.value})")
        await self._SendUnchecked(message)

    async def Close(self, reason: str = "closed locally") -> None:
        if self._closed.is_set():
            return
        self.closeReason = reason
        self._closed.set()
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current:
                task.cancel()
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass

    async def _SendUnchecked(self, message: Message) -> None:
        async with self._sendLock:
            await WriteFrame(self._writer, int(message.MESSAGE_TYPE), message.ToPayload())

    async def _ReceiveLoop(self) -> None:
        reason = "receive loop ended"
        decoder = FrameDecoder()
        try:
            while True:
                data = await self._reader.read(RECEIVE_CHUNK_BYTES)
                if not data:
                    decoder.Finish()
                    reason = "peer closed the connection"
                    break
                self._lastHeardNs = time.monotonic_ns()
                for frame in decoder.Feed(data):
                    await self._Dispatch(DecodeMessage(frame))
        except asyncio.CancelledError:
            raise
        except ProtocolError as error:
            reason = f"protocol error: {error}"
        except (OSError, ssl.SSLError) as error:
            reason = f"connection error: {error}"
        except Exception as error:
            LOG.exception("handler for %s failed", self.peer.node)
            reason = f"handler failed: {error}"
        await self.Close(reason)

    async def _Dispatch(self, message: Message) -> None:
        messageType = message.MESSAGE_TYPE
        if messageType is MessageType.HELLO:
            raise ProtocolError("HELLO is only valid at the start of a connection")
        if not IsAllowed(messageType, not self._localIsHub, self._nodeRole):
            raise MessageNotAllowed(f"{messageType.name} is not allowed from {self.peer.node} ({self.peer.role.value})")
        if isinstance(message, Heartbeat):
            await self._OnHeartbeat(message)
        elif self._onMessage is not None:
            await self._onMessage(self, message)

    async def _OnHeartbeat(self, heartbeat: Heartbeat) -> None:
        if not heartbeat.echo:
            await self._SendUnchecked(Heartbeat(heartbeat.sequence, heartbeat.sentAtNs, echo=True))
            return
        elapsedNs = time.monotonic_ns() - heartbeat.sentAtNs
        if elapsedNs >= 0:
            self.rttMs = elapsedNs / NS_PER_MS

    async def _HeartbeatLoop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._settings.heartbeatIntervalS)
                self._sequence += 1
                await self._SendUnchecked(Heartbeat(self._sequence, time.monotonic_ns()))
        except asyncio.CancelledError:
            raise
        except (OSError, ssl.SSLError) as error:
            await self.Close(f"connection error: {error}")

    async def _WatchdogLoop(self) -> None:
        timeoutNs = int(self._settings.heartbeatTimeoutS * 1e9)
        period = self._settings.heartbeatTimeoutS / WATCHDOG_CHECKS_PER_TIMEOUT
        while True:
            await asyncio.sleep(period)
            if time.monotonic_ns() - self._lastHeardNs > timeoutNs:
                await self.Close("heartbeat timeout")
                return


def _ExpectHello(frame: Frame | None, who: str) -> Hello:
    if frame is None:
        raise HandshakeRejected(f"{who} closed the connection before sending HELLO")
    if frame.messageType != MessageType.HELLO:
        raise HandshakeRejected(f"{who} sent message type {frame.messageType} instead of HELLO")
    hello = DecodeMessage(frame)
    assert isinstance(hello, Hello)
    if hello.protocolVersion != PROTOCOL_VERSION:
        raise HandshakeRejected(f"{who} speaks protocol {hello.protocolVersion}, this node speaks {PROTOCOL_VERSION}")
    return hello


async def AcceptPeer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, config: ClusterConfig, onMessage: MessageHandler | None) -> Connection:
    """Hub side of the handshake; raises if the peer is not who its certificate and the configuration say."""
    sslObject = writer.get_extra_info("ssl_object")
    certificateName = PeerNodeName(sslObject)
    hello = _ExpectHello(await asyncio.wait_for(ReadFrame(reader), config.network.connectTimeoutS), certificateName)
    if hello.node != certificateName:
        raise HandshakeRejected(f"certificate says '{certificateName}' but HELLO says '{hello.node}'")
    allowedRoles = config.Node(certificateName).roles if certificateName in config.nodes else frozenset()
    if hello.role == Role.COORDINATOR or hello.role not in allowedRoles:
        raise HandshakeRejected(f"node '{certificateName}' is not allowed to connect as '{hello.role.value}'")
    await WriteFrame(writer, int(MessageType.HELLO), Hello(PROTOCOL_VERSION, config.hub.node, Role.COORDINATOR).ToPayload())
    return Connection(reader, writer, PeerInfo(hello.node, hello.role, DescribeSession(sslObject)), True, hello.role, config.network, onMessage)


async def DialHub(config: ClusterConfig, nodeName: str, role: Role, onMessage: MessageHandler | None = None, sslContext: ssl.SSLContext | None = None) -> Connection:
    """One connection attempt from a node to the hub; the caller owns retries (see `NodeClient`)."""
    if role not in config.Node(nodeName).roles:
        raise HandshakeRejected(f"node '{nodeName}' is not configured with role '{role.value}'")
    context = sslContext or ClientContextFor(config, nodeName)
    timeout = config.network.connectTimeoutS
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(config.hub.address, config.hub.port, ssl=context, server_hostname=config.hub.node, ssl_handshake_timeout=timeout),
        timeout,
    )
    try:
        sslObject = writer.get_extra_info("ssl_object")
        hubName = PeerNodeName(sslObject)
        await WriteFrame(writer, int(MessageType.HELLO), Hello(PROTOCOL_VERSION, nodeName, role).ToPayload())
        hello = _ExpectHello(await asyncio.wait_for(ReadFrame(reader), timeout), "the hub")
        if hello.node != hubName or hello.node != config.hub.node or hello.role != Role.COORDINATOR:
            raise HandshakeRejected(f"unexpected hub identity '{hello.node}' as '{hello.role.value}'")
    except BaseException:
        writer.close()
        raise
    return Connection(reader, writer, PeerInfo(hello.node, hello.role, DescribeSession(sslObject)), False, role, config.network, onMessage)


class HubServer:
    """Listens for nodes, keeps one live connection per (node, role) and reports arrivals and losses."""

    def __init__(
        self,
        config: ClusterConfig,
        onMessage: MessageHandler | None = None,
        onPeerConnected: PeerHandler | None = None,
        onPeerLost: LostHandler | None = None,
        sslContext: ssl.SSLContext | None = None,
    ) -> None:
        self.config = config
        self.rejections: list[str] = []
        self._onMessage = onMessage
        self._onPeerConnected = onPeerConnected
        self._onPeerLost = onPeerLost
        self._sslContext = sslContext
        self._server: asyncio.Server | None = None
        self._peers: dict[tuple[str, Role], Connection] = {}

    @property
    def peers(self) -> dict[tuple[str, Role], Connection]:
        return dict(self._peers)

    async def Start(self) -> None:
        context = self._sslContext or ServerContextFor(self.config, self.config.hub.node)
        self._server = await asyncio.start_server(
            self._OnClient, self.config.hub.bindAddress, self.config.hub.port, ssl=context, ssl_handshake_timeout=self.config.network.connectTimeoutS
        )

    async def Stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for connection in list(self._peers.values()):
            await connection.Close("hub stopped")
        if self._server is not None:
            await self._server.wait_closed()

    async def Send(self, node: str, role: Role, message: Message) -> None:
        connection = self._peers.get((node, role))
        if connection is None or not connection.isOpen:
            raise ConnectionError(f"{node} ({role.value}) is not connected")
        await connection.Send(message)

    async def _OnClient(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            connection = await AcceptPeer(reader, writer, self.config, self._onMessage)
        except (ProtocolError, PeerIdentityError, asyncio.TimeoutError, OSError, ssl.SSLError) as error:
            self._Reject(str(error) or type(error).__name__)
            writer.close()
            return
        await self._Register(connection)
        connection.Start()
        await connection.Wait()
        await self._Unregister(connection)

    def _Reject(self, reason: str) -> None:
        LOG.warning("rejected connection: %s", reason)
        self.rejections.append(reason)

    async def _Register(self, connection: Connection) -> None:
        key = (connection.peer.node, connection.peer.role)
        previous = self._peers.get(key)
        self._peers[key] = connection
        if previous is not None:
            await previous.Close("replaced by a newer connection")
        LOG.info("%s (%s) connected over %s", connection.peer.node, connection.peer.role.value, connection.peer.session)
        if self._onPeerConnected is not None:
            await self._onPeerConnected(connection)

    async def _Unregister(self, connection: Connection) -> None:
        key = (connection.peer.node, connection.peer.role)
        if self._peers.get(key) is connection:
            del self._peers[key]
        LOG.info("%s (%s) lost: %s", connection.peer.node, connection.peer.role.value, connection.closeReason)
        if self._onPeerLost is not None:
            await self._onPeerLost(connection, connection.closeReason or "closed")


class NodeClient:
    """Keeps a link to the hub alive: dials, waits until it drops, and retries with exponential backoff."""

    def __init__(
        self,
        config: ClusterConfig,
        nodeName: str,
        role: Role,
        onMessage: MessageHandler | None = None,
        onConnected: PeerHandler | None = None,
        onDisconnected: LostHandler | None = None,
        sslContext: ssl.SSLContext | None = None,
    ) -> None:
        self.config = config
        self.nodeName = nodeName
        self.role = role
        self.connection: Connection | None = None
        self._onMessage = onMessage
        self._onConnected = onConnected
        self._onDisconnected = onDisconnected
        self._sslContext = sslContext
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()

    async def WaitConnected(self, timeout: float) -> Connection:
        await asyncio.wait_for(self._connected.wait(), timeout)
        assert self.connection is not None
        return self.connection

    async def Stop(self) -> None:
        self._stop.set()
        if self.connection is not None:
            await self.connection.Close("node stopped")

    async def Run(self) -> None:
        settings = self.config.network
        delay = settings.reconnectInitialS
        while not self._stop.is_set():
            try:
                connection = await DialHub(self.config, self.nodeName, self.role, self._onMessage, self._sslContext)
            except (ProtocolError, PeerIdentityError, asyncio.TimeoutError, OSError, ssl.SSLError) as error:
                LOG.warning("%s could not reach the hub: %s", self.nodeName, str(error) or type(error).__name__)
                await self._SleepUnlessStopped(delay)
                delay = min(delay * 2, settings.reconnectMaxS)
                continue
            delay = settings.reconnectInitialS
            await self._Serve(connection)
            await self._SleepUnlessStopped(delay)

    async def _Serve(self, connection: Connection) -> None:
        self.connection = connection
        connection.Start()
        self._connected.set()
        if self._onConnected is not None:
            await self._onConnected(connection)
        await connection.Wait()
        self._connected.clear()
        self.connection = None
        if self._onDisconnected is not None:
            await self._onDisconnected(connection, connection.closeReason or "closed")

    async def _SleepUnlessStopped(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), seconds)
        except asyncio.TimeoutError:
            pass
