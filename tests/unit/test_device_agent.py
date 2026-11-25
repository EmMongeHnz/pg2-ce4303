"""DeviceAgentService against a real hub over mutual TLS: input events up, camera state and models down."""

import asyncio
import os
import socket
import time
from pathlib import Path

import pytest

from astrea_common.compression import ZstdCompress
from astrea_common.config import ClusterConfig, ParseConfig
from astrea_common.holo_event import EncodeHoloEvent, EventValue, ButtonCode
from astrea_common.link import HubServer
from astrea_common.messages import CameraState, Compression, InputEvent, InputSource, ModelPush, Role, SplitModel
from astrea_common.render_link import STATE_STRUCT, DecodeState
from device_agent.service import DeviceAgentService

WAIT_S = 5.0


def FreePort() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def MakeConfig(pki, port: int) -> ClusterConfig:
    table = {
        "hub": {"node": "host", "address": "127.0.0.1", "port": port},
        "tls": {"ca-file": str(pki.ca), "cert-file": str(pki.directory / "{node}.crt"), "key-file": str(pki.directory / "{node}.key")},
        "network": {"heartbeat-interval-s": 0.1, "heartbeat-timeout-s": 0.6, "connect-timeout-s": 2.0, "reconnect-initial-s": 0.05, "reconnect-max-s": 0.2},
        "nodes": {"host": {"roles": ["coordinator"]}, "worker-b": {"roles": ["worker"]}, "pi": {"roles": ["worker", "device-agent"]}},
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


def ButtonEvent(code: ButtonCode, value: EventValue, timestampNs: int = 1) -> InputEvent:
    return InputEvent(InputSource.JOYSTICK, code, value, timestampNs)


class Harness:
    def __init__(self, pki, tmp_path: Path, withDevice: bool = True) -> None:
        self.config = MakeConfig(pki, FreePort())
        self.received: list = []
        self.hub = HubServer(self.config, onMessage=self._OnHubMessage)
        self.devicePath = tmp_path / "holo-input"
        self.socketPath = tmp_path / "r.sock"
        self.modelsDir = tmp_path / "models"
        if withDevice:
            os.mkfifo(self.devicePath)
        self.agent = DeviceAgentService(self.config, "pi", self.socketPath, self.modelsDir, self.devicePath if withDevice else None)
        self._runner: asyncio.Task | None = None

    async def _OnHubMessage(self, connection, message) -> None:
        self.received.append(message)

    async def __aenter__(self) -> "Harness":
        await self.hub.Start()
        self._runner = asyncio.create_task(self.agent.Run())
        await self.agent.WaitConnected(WAIT_S)
        await WaitUntil(lambda: ("pi", Role.DEVICE_AGENT) in self.hub.peers)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.agent.Stop()
        await self._runner
        await self.hub.Stop()

    async def Renderer(self):
        await WaitUntil(self.socketPath.exists)
        return await asyncio.open_unix_connection(str(self.socketPath))

    async def OpenDeviceWriter(self) -> int:
        async def attempt():
            while True:
                try:
                    return os.open(self.devicePath, os.O_WRONLY | os.O_NONBLOCK)
                except OSError:
                    await asyncio.sleep(0.02)

        return await asyncio.wait_for(attempt(), WAIT_S)

    async def SendToAgent(self, message) -> None:
        await self.hub.Send("pi", Role.DEVICE_AGENT, message)


async def ReadState(reader: asyncio.StreamReader) -> CameraState:
    return DecodeState(await asyncio.wait_for(reader.readexactly(STATE_STRUCT.size), timeout=WAIT_S))


def test_button_events_reach_the_host_unchanged(pki, tmp_path):
    async def scenario():
        async with Harness(pki, tmp_path) as harness:
            writer = await harness.OpenDeviceWriter()
            events = [ButtonEvent(ButtonCode.RIGHT, EventValue.PRESS, 11), ButtonEvent(ButtonCode.RIGHT, EventValue.REPEAT, 12), ButtonEvent(ButtonCode.RIGHT, EventValue.RELEASE, 13)]
            os.write(writer, b"".join(EncodeHoloEvent(event) for event in events))
            await WaitUntil(lambda: len([m for m in harness.received if isinstance(m, InputEvent)]) == 3)
            os.close(writer)
            return events, [m for m in harness.received if isinstance(m, InputEvent)], harness.agent.eventsForwarded

    events, received, forwarded = asyncio.run(scenario())
    assert received == events
    assert forwarded == 3


def test_a_record_split_across_two_reads_is_rebuilt(pki, tmp_path):
    async def scenario():
        async with Harness(pki, tmp_path) as harness:
            writer = await harness.OpenDeviceWriter()
            record = EncodeHoloEvent(ButtonEvent(ButtonCode.ZOOM_IN, EventValue.PRESS, 5))
            os.write(writer, record[:7])
            await asyncio.sleep(0.15)
            os.write(writer, record[7:])
            await WaitUntil(lambda: any(isinstance(m, InputEvent) for m in harness.received))
            os.close(writer)
            return [m for m in harness.received if isinstance(m, InputEvent)]

    assert asyncio.run(scenario()) == [ButtonEvent(ButtonCode.ZOOM_IN, EventValue.PRESS, 5)]


def test_camera_state_goes_to_the_renderer_and_a_new_renderer_gets_the_latest(pki, tmp_path):
    async def scenario():
        async with Harness(pki, tmp_path, withDevice=False) as harness:
            reader, writer = await harness.Renderer()
            await harness.SendToAgent(CameraState(10.0, 20.0, 1.5, "cup"))
            first = await ReadState(reader)
            await harness.SendToAgent(CameraState(11.0, 20.0, 1.5, "cup"))
            second = await ReadState(reader)
            writer.close()
            reader2, writer2 = await harness.Renderer()
            replay = await ReadState(reader2)
            writer2.close()
            return first, second, replay

    first, second, replay = asyncio.run(scenario())
    assert (first.yaw, first.modelId) == (10.0, "cup")
    assert second.yaw == 11.0
    assert replay.yaw == 11.0


@pytest.mark.parametrize("compression", [Compression.NONE, Compression.ZSTD])
def test_a_pushed_model_is_installed_and_the_state_is_repeated(pki, tmp_path, compression):
    model = os.urandom(3000) + b"glTF" * 500

    async def scenario():
        async with Harness(pki, tmp_path, withDevice=False) as harness:
            reader, writer = await harness.Renderer()
            await harness.SendToAgent(CameraState(0.0, 20.0, 1.0, "bunny"))
            await ReadState(reader)
            payload = ZstdCompress(model) if compression is Compression.ZSTD else model
            for chunk in reversed(SplitModel("bunny", payload, compression, chunkBytes=1000)):
                await harness.SendToAgent(chunk)
            repeated = await ReadState(reader)
            writer.close()
            return repeated, (harness.modelsDir / "bunny.glb").read_bytes(), sorted(path.name for path in harness.modelsDir.iterdir())

    repeated, installed, files = asyncio.run(scenario())
    assert repeated.modelId == "bunny"
    assert installed == model
    assert files == ["bunny.glb"]


def test_a_model_with_a_bad_hash_is_not_installed(pki, tmp_path):
    async def scenario():
        async with Harness(pki, tmp_path, withDevice=False) as harness:
            chunks = SplitModel("bunny", b"x" * 100)
            bad = ModelPush("bunny", 0, 1, 100, "0" * 64, Compression.NONE, b"x" * 100)
            await harness.SendToAgent(bad)
            for chunk in chunks:
                await harness.SendToAgent(chunk)
            await WaitUntil(lambda: harness.agent.modelsInstalled == 1)
            return (harness.modelsDir / "bunny.glb").read_bytes()

    assert asyncio.run(scenario()) == b"x" * 100


@pytest.mark.parametrize("modelId", ["../escape", "a/b", ".hidden", "x" * 40, "with space", "ñandú"])
def test_models_with_unusable_ids_are_ignored(pki, tmp_path, modelId):
    async def scenario():
        async with Harness(pki, tmp_path, withDevice=False) as harness:
            for chunk in SplitModel(modelId, b"data"):
                await harness.SendToAgent(chunk)
            await SplitModelBarrier(harness)
            return harness.agent.modelsInstalled, list(harness.modelsDir.rglob("*")), list(tmp_path.glob("*.glb"))

    installed, inside, outside = asyncio.run(scenario())
    assert installed == 0 and inside == [] and outside == []


async def SplitModelBarrier(harness: Harness) -> None:
    """A valid model sent afterwards is installed only once the earlier chunks were handled, in order."""
    for chunk in SplitModel("barrier", b"ok"):
        await harness.SendToAgent(chunk)
    await WaitUntil(lambda: (harness.modelsDir / "barrier.glb").exists())
    (harness.modelsDir / "barrier.glb").unlink()
    harness.agent.modelsInstalled -= 1


def test_events_are_dropped_while_the_host_is_unreachable(pki, tmp_path):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        devicePath = tmp_path / "holo-input"
        os.mkfifo(devicePath)
        agent = DeviceAgentService(config, "pi", tmp_path / "r.sock", tmp_path / "models", devicePath)
        runner = asyncio.create_task(agent.Run())
        try:
            async def open_writer():
                while True:
                    try:
                        return os.open(devicePath, os.O_WRONLY | os.O_NONBLOCK)
                    except OSError:
                        await asyncio.sleep(0.02)

            writer = await asyncio.wait_for(open_writer(), WAIT_S)
            os.write(writer, EncodeHoloEvent(ButtonEvent(ButtonCode.LEFT, EventValue.PRESS)))
            await WaitUntil(lambda: agent.eventsDropped == 1)
            os.close(writer)
            return agent.eventsForwarded, agent.eventsDropped
        finally:
            await agent.Stop()
            await runner

    assert asyncio.run(scenario()) == (0, 1)


def test_the_agent_waits_for_a_device_that_appears_later(pki, tmp_path):
    async def scenario():
        harness = Harness(pki, tmp_path, withDevice=False)
        harness.agent = DeviceAgentService(harness.config, "pi", harness.socketPath, harness.modelsDir, harness.devicePath, deviceRetryS=0.05)
        async with harness:
            await asyncio.sleep(0.2)
            os.mkfifo(harness.devicePath)
            writer = await harness.OpenDeviceWriter()
            os.write(writer, EncodeHoloEvent(ButtonEvent(ButtonCode.AUTOROTATE, EventValue.PRESS, 9)))
            await WaitUntil(lambda: any(isinstance(m, InputEvent) for m in harness.received))
            os.close(writer)
            return [m for m in harness.received if isinstance(m, InputEvent)]

    assert asyncio.run(scenario()) == [ButtonEvent(ButtonCode.AUTOROTATE, EventValue.PRESS, 9)]
