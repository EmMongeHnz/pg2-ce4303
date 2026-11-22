"""Hub and nodes over real mutual-TLS sockets on loopback: handshake rules, heartbeats, failures, wire contents."""

import asyncio
import socket
import ssl
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from astrea_common.config import ClusterConfig, ParseConfig
from astrea_common.framing import ReadFrame, WriteFrame
from astrea_common.link import HubServer, MessageNotAllowed, NodeClient
from astrea_common.messages import (
    PROTOCOL_VERSION,
    CameraState,
    GridPayload,
    Heartbeat,
    Hello,
    InputEvent,
    JobAssign,
    MessageType,
    Role,
    RotateDone,
)
from astrea_common.tls import BuildClientContext, BuildServerContext

HEARTBEAT_INTERVAL_S = 0.1
HEARTBEAT_TIMEOUT_S = 0.6
WAIT_S = 5.0
SAMPLE_CAMERA = CameraState(10.0, 20.0, 1.5, "bunny")


def FreePort() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def MakeConfig(pki, port: int, nodes: dict | None = None) -> ClusterConfig:
    table = {
        "hub": {"node": "host", "address": "127.0.0.1", "port": port},
        "tls": {"ca-file": str(pki.ca), "cert-file": str(pki.directory / "{node}.crt"), "key-file": str(pki.directory / "{node}.key")},
        "network": {
            "heartbeat-interval-s": HEARTBEAT_INTERVAL_S,
            "heartbeat-timeout-s": HEARTBEAT_TIMEOUT_S,
            "connect-timeout-s": 2.0,
            "reconnect-initial-s": 0.05,
            "reconnect-max-s": 0.2,
        },
        "nodes": nodes or {"host": {"roles": ["coordinator"]}, "worker-b": {"roles": ["worker"]}, "pi": {"roles": ["worker", "device-agent"]}},
    }
    return ParseConfig(table, Path("/unused"))


async def WaitUntil(predicate, timeout: float = WAIT_S):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached in time")


@asynccontextmanager
async def RunningHub(config: ClusterConfig, **handlers):
    hub = HubServer(config, **handlers)
    await hub.Start()
    try:
        yield hub
    finally:
        await hub.Stop()


@asynccontextmanager
async def RunningNode(config: ClusterConfig, name: str, role: Role, **handlers):
    client = NodeClient(config, name, role, **handlers)
    runner = asyncio.create_task(client.Run())
    try:
        yield client
    finally:
        await client.Stop()
        await runner


async def RawNode(pki, config: ClusterConfig, certNode: str, hello: Hello):
    context = BuildClientContext(pki.ca, pki.Cert(certNode), pki.Key(certNode))
    reader, writer = await asyncio.open_connection(config.hub.address, config.hub.port, ssl=context, server_hostname="host")
    await WriteFrame(writer, int(MessageType.HELLO), hello.ToPayload())
    return reader, writer


async def ReadUntilClosed(reader: asyncio.StreamReader, timeout: float = WAIT_S) -> bool:
    """True when the peer closes the stream (cleanly or with a reset) within `timeout`."""
    async def drain():
        try:
            while await reader.read(4096):
                pass
        except (ConnectionError, ssl.SSLError, OSError):
            pass

    await asyncio.wait_for(drain(), timeout)
    return True


def test_three_nodes_exchange_heartbeats_over_tls13(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        async with RunningHub(config) as hub:
            async with RunningNode(config, "worker-b", Role.WORKER) as workerB, RunningNode(config, "pi", Role.DEVICE_AGENT) as pi:
                await workerB.WaitConnected(WAIT_S)
                await pi.WaitConnected(WAIT_S)
                await WaitUntil(lambda: all(c.rttMs is not None for c in hub.peers.values()) and len(hub.peers) == 2)
                await WaitUntil(lambda: workerB.connection.rttMs is not None and pi.connection.rttMs is not None)
                sessions = {c.peer.session.split()[0] for c in hub.peers.values()} | {workerB.connection.peer.session.split()[0]}
                return sessions, {key[0] for key in hub.peers}, workerB.connection.peer.node

    sessions, names, hubName = asyncio.run(scenario())
    assert sessions == {"TLSv1.3"} and names == {"worker-b", "pi"} and hubName == "host"


def test_one_node_can_hold_two_roles_at_once(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        async with RunningHub(config) as hub:
            async with RunningNode(config, "pi", Role.WORKER) as asWorker, RunningNode(config, "pi", Role.DEVICE_AGENT) as asAgent:
                await asWorker.WaitConnected(WAIT_S)
                await asAgent.WaitConnected(WAIT_S)
                return set(hub.peers)

    assert asyncio.run(scenario()) == {("pi", Role.WORKER), ("pi", Role.DEVICE_AGENT)}


def test_messages_flow_in_their_allowed_directions(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        atHub, atPi = [], []

        async def hubHandler(connection, message):
            atHub.append(message)

        async def piHandler(connection, message):
            atPi.append(message)

        async with RunningHub(config, onMessage=hubHandler) as hub:
            async with RunningNode(config, "pi", Role.DEVICE_AGENT, onMessage=piHandler) as pi:
                connection = await pi.WaitConnected(WAIT_S)
                await WaitUntil(lambda: ("pi", Role.DEVICE_AGENT) in hub.peers)
                event = InputEvent(1, 2, 1, 42)
                await connection.Send(event)
                await hub.Send("pi", Role.DEVICE_AGENT, SAMPLE_CAMERA)
                await WaitUntil(lambda: atHub and atPi)
                with pytest.raises(MessageNotAllowed):
                    await hub.Send("pi", Role.DEVICE_AGENT, InputEvent(1, 2, 1, 42))
                with pytest.raises(MessageNotAllowed):
                    await connection.Send(SAMPLE_CAMERA)
                with pytest.raises(MessageNotAllowed):
                    await connection.Send(RotateDone(1, 0))
                return atHub, atPi

    atHub, atPi = asyncio.run(scenario())
    assert atHub == [InputEvent(1, 2, 1, 42)] and atPi == [SAMPLE_CAMERA]


def test_hub_refuses_to_send_a_job_to_a_device_agent(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        async with RunningHub(config) as hub:
            async with RunningNode(config, "pi", Role.DEVICE_AGENT) as pi:
                await pi.WaitConnected(WAIT_S)
                await WaitUntil(lambda: ("pi", Role.DEVICE_AGENT) in hub.peers)
                with pytest.raises(MessageNotAllowed):
                    await hub.Send("pi", Role.DEVICE_AGENT, JobAssign("job", 0, 1, GridPayload((0.0, 0.0, 0.0), 1.0, (1, 1, 1)), 1.0, {}, ()))
                with pytest.raises(ConnectionError):
                    await hub.Send("worker-b", Role.WORKER, SAMPLE_CAMERA)

    asyncio.run(scenario())


async def ExpectRejection(pki, hello: Hello, certNode: str = "pi", hubNodes: dict | None = None) -> list[str]:
    config = MakeConfig(pki, FreePort(), hubNodes)
    async with RunningHub(config) as hub:
        reader, writer = await RawNode(pki, config, certNode, hello)
        try:
            frame = None
            try:
                frame = await asyncio.wait_for(ReadFrame(reader), WAIT_S)
            except (ConnectionError, ssl.SSLError, OSError):
                pass
            assert frame is None
            await WaitUntil(lambda: hub.rejections)
        finally:
            writer.close()
        assert not hub.peers
        return hub.rejections


def test_hello_naming_another_node_than_the_certificate_is_rejected(pki):
    rejections = asyncio.run(ExpectRejection(pki, Hello(PROTOCOL_VERSION, "worker-b", Role.WORKER), certNode="pi"))
    assert "certificate says 'pi' but HELLO says 'worker-b'" in rejections[0]


def test_role_not_allowed_for_the_node_is_rejected(pki):
    rejections = asyncio.run(ExpectRejection(pki, Hello(PROTOCOL_VERSION, "worker-b", Role.DEVICE_AGENT), certNode="worker-b"))
    assert "not allowed to connect as 'device-agent'" in rejections[0]


def test_connecting_as_coordinator_is_rejected(pki):
    rejections = asyncio.run(ExpectRejection(pki, Hello(PROTOCOL_VERSION, "pi", Role.COORDINATOR)))
    assert "not allowed to connect as 'coordinator'" in rejections[0]


def test_node_missing_from_the_hub_configuration_is_rejected(pki):
    nodes = {"host": {"roles": ["coordinator"]}, "worker-b": {"roles": ["worker"]}}
    rejections = asyncio.run(ExpectRejection(pki, Hello(PROTOCOL_VERSION, "pi", Role.WORKER), hubNodes=nodes))
    assert "not allowed to connect" in rejections[0]


def test_incompatible_protocol_version_is_rejected(pki):
    rejections = asyncio.run(ExpectRejection(pki, Hello(PROTOCOL_VERSION + 1, "pi", Role.WORKER)))
    assert "protocol" in rejections[0]


def test_first_frame_other_than_hello_is_rejected(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        async with RunningHub(config) as hub:
            context = BuildClientContext(pki.ca, pki.Cert("pi"), pki.Key("pi"))
            reader, writer = await asyncio.open_connection(config.hub.address, config.hub.port, ssl=context, server_hostname="host")
            await WriteFrame(writer, int(MessageType.HEARTBEAT), Heartbeat(1, 1).ToPayload())
            await WaitUntil(lambda: hub.rejections)
            writer.close()
            return hub.rejections

    assert "instead of HELLO" in asyncio.run(scenario())[0]


@pytest.mark.parametrize(
    "frame",
    [
        (MessageType.INPUT_EVENT, InputEvent(1, 1, 1, 1).ToPayload()),
        (MessageType.ROTATE_DONE, RotateDone(1, 0).ToPayload()),
        (MessageType.HELLO, Hello(PROTOCOL_VERSION, "worker-b", Role.WORKER).ToPayload()),
    ],
)
def test_message_outside_its_link_closes_the_connection(pki, frame):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        lost = []

        async def onLost(connection, reason):
            lost.append(reason)

        async with RunningHub(config, onPeerLost=onLost) as hub:
            reader, writer = await RawNode(pki, config, "worker-b", Hello(PROTOCOL_VERSION, "worker-b", Role.WORKER))
            await ReadFrame(reader)
            await WaitUntil(lambda: ("worker-b", Role.WORKER) in hub.peers)
            await WriteFrame(writer, int(frame[0]), frame[1])
            await WaitUntil(lambda: lost)
            writer.close()
            return lost[0]

    reason = asyncio.run(scenario())
    assert "not allowed" in reason or "HELLO is only valid" in reason


def test_garbage_after_the_handshake_closes_the_connection(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        lost = []

        async def onLost(connection, reason):
            lost.append(reason)

        async with RunningHub(config, onPeerLost=onLost) as hub:
            reader, writer = await RawNode(pki, config, "worker-b", Hello(PROTOCOL_VERSION, "worker-b", Role.WORKER))
            await ReadFrame(reader)
            await WaitUntil(lambda: ("worker-b", Role.WORKER) in hub.peers)
            writer.write(b"\xff\xff\xff\xff\x04garbage")
            await writer.drain()
            await WaitUntil(lambda: lost)
            writer.close()
            return lost[0]

    assert "protocol error" in asyncio.run(scenario())


def test_silent_node_is_dropped_after_the_heartbeat_timeout(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        lost = []

        async def onLost(connection, reason):
            lost.append((reason, time.monotonic()))

        async with RunningHub(config, onPeerLost=onLost) as hub:
            reader, writer = await RawNode(pki, config, "worker-b", Hello(PROTOCOL_VERSION, "worker-b", Role.WORKER))
            await ReadFrame(reader)
            started = time.monotonic()
            await WaitUntil(lambda: lost)
            writer.close()
            return lost[0][0], lost[0][1] - started

    reason, elapsed = asyncio.run(scenario())
    assert reason == "heartbeat timeout"
    assert HEARTBEAT_TIMEOUT_S * 0.5 < elapsed < HEARTBEAT_TIMEOUT_S * 4


def test_node_detects_a_silent_hub_and_reconnects(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        serverContext = BuildServerContext(pki.ca, pki.Cert("host"), pki.Key("host"))
        accepted = []

        async def silentHub(reader, writer):
            await ReadFrame(reader)
            await WriteFrame(writer, int(MessageType.HELLO), Hello(PROTOCOL_VERSION, "host", Role.COORDINATOR).ToPayload())
            accepted.append(writer)
            await asyncio.sleep(WAIT_S)

        server = await asyncio.start_server(silentHub, "127.0.0.1", config.hub.port, ssl=serverContext)
        reasons = []

        async def onDisconnected(connection, reason):
            reasons.append(reason)

        try:
            async with RunningNode(config, "worker-b", Role.WORKER, onDisconnected=onDisconnected):
                await WaitUntil(lambda: reasons and len(accepted) >= 2)
        finally:
            server.close()
            for writer in accepted:
                writer.close()
        return reasons[0]

    assert asyncio.run(scenario()) == "heartbeat timeout"


def test_node_reconnects_after_the_hub_restarts(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        async with RunningNode(config, "worker-b", Role.WORKER) as node:
            async with RunningHub(config) as hub:
                first = await node.WaitConnected(WAIT_S)
                await WaitUntil(lambda: ("worker-b", Role.WORKER) in hub.peers)
            await WaitUntil(lambda: not first.isOpen)
            async with RunningHub(config) as secondHub:
                await WaitUntil(lambda: ("worker-b", Role.WORKER) in secondHub.peers)
                second = await node.WaitConnected(WAIT_S)
                return first is not second and second.isOpen

    assert asyncio.run(scenario())


def test_new_connection_of_the_same_node_replaces_the_old_one(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        async with RunningHub(config) as hub:
            async with RunningNode(config, "worker-b", Role.WORKER) as first:
                firstConnection = await first.WaitConnected(WAIT_S)
                await WaitUntil(lambda: ("worker-b", Role.WORKER) in hub.peers)
                oldHubSide = hub.peers[("worker-b", Role.WORKER)]
                async with RunningNode(config, "worker-b", Role.WORKER) as second:
                    await second.WaitConnected(WAIT_S)
                    await WaitUntil(lambda: not oldHubSide.isOpen)
                    return oldHubSide.closeReason, hub.peers[("worker-b", Role.WORKER)] is not oldHubSide

    reason, replaced = asyncio.run(scenario())
    assert reason == "replaced by a newer connection" and replaced


def ParseTlsRecords(stream: bytes) -> list[int]:
    """Splits a captured byte stream into TLS records; fails if it is not made only of well-formed records."""
    types, offset = [], 0
    while offset < len(stream):
        assert offset + 5 <= len(stream), "stream ends inside a record header"
        recordType, versionMajor, versionMinor = stream[offset], stream[offset + 1], stream[offset + 2]
        length = int.from_bytes(stream[offset + 3 : offset + 5], "big")
        assert recordType in (0x14, 0x15, 0x16, 0x17), f"not a TLS record type: {recordType:#x}"
        assert (versionMajor, versionMinor) in ((3, 1), (3, 3)), "not a TLS record version"
        offset += 5 + length
        types.append(recordType)
    assert offset == len(stream), "stream ends inside a record body"
    return types


def test_everything_on_the_wire_is_tls_and_hides_the_protocol(pki):
    async def scenario():
        hubPort, proxyPort = FreePort(), FreePort()
        hubConfig, nodeConfig = MakeConfig(pki, hubPort), MakeConfig(pki, proxyPort)
        captured = {"up": bytearray(), "down": bytearray()}

        async def pipe(source, sink, key):
            try:
                while data := await source.read(4096):
                    captured[key].extend(data)
                    sink.write(data)
                    await sink.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                sink.close()

        async def proxy(clientReader, clientWriter):
            serverReader, serverWriter = await asyncio.open_connection("127.0.0.1", hubPort)
            await asyncio.gather(pipe(clientReader, serverWriter, "up"), pipe(serverReader, clientWriter, "down"))

        proxyServer = await asyncio.start_server(proxy, "127.0.0.1", proxyPort)
        try:
            async with RunningHub(hubConfig) as hub:
                async with RunningNode(nodeConfig, "worker-b", Role.WORKER) as node:
                    await node.WaitConnected(WAIT_S)
                    await WaitUntil(lambda: node.connection.rttMs is not None)
                    await asyncio.sleep(2 * HEARTBEAT_INTERVAL_S)
        finally:
            proxyServer.close()
        return bytes(captured["up"]), bytes(captured["down"])

    up, down = asyncio.run(scenario())
    assert up[:2] == b"\x16\x03" and down[:2] == b"\x16\x03"
    for stream in (up, down):
        assert 0x17 in ParseTlsRecords(stream)
        for secret in (b"worker-b", b"protocolVersion", b"sentAtNs", b"HELLO", b"coordinator"):
            assert secret not in stream


def test_shipped_config_and_example_certificates_work_together():
    from dataclasses import replace

    from astrea_common.config import LoadConfig

    repoRoot = Path(__file__).resolve().parents[2]
    shipped = LoadConfig(repoRoot / "config.toml")
    config = replace(shipped, hub=replace(shipped.hub, port=FreePort()))

    async def scenario():
        async with RunningHub(config) as hub:
            async with RunningNode(config, "worker-b", Role.WORKER) as node:
                connection = await node.WaitConnected(WAIT_S)
                await WaitUntil(lambda: ("worker-b", Role.WORKER) in hub.peers)
                return connection.peer.session

    assert asyncio.run(scenario()).startswith("TLSv1.3")
