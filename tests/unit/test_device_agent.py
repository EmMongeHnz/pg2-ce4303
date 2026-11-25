"""The C device agent (net/build/astrea-device-agent) against a hub over mutual TLS: input events up, camera state and
models down. The hub is the Python HubServer, which runs the C network daemon."""

import asyncio
import hashlib
import os
import signal
import time
from pathlib import Path

import pytest

from astrea_common.holo_event import ButtonCode, EncodeHoloEvent, EventValue
from astrea_common.link import HubServer
from astrea_common.messages import CameraState, Compression, InputEvent, InputSource, ModelPush, Role, SplitModel
from astrea_common.net_daemon import FindProgram
from astrea_common.render_link import STATE_STRUCT, DecodeState
from test_secure_link import FreePort, MakeConfig, WaitUntil

WAIT_S = 8.0
AGENT = FindProgram("astrea-device-agent", "ASTREA_DEVICE_AGENT")


def ButtonEvent(code: ButtonCode, value: EventValue, timestampNs: int = 1) -> InputEvent:
    return InputEvent(InputSource.JOYSTICK, code, value, timestampNs)


class Harness:
    def __init__(self, pki, tmp_path: Path, withDevice: bool = True, startHub: bool = True, deviceFirst: bool = True) -> None:
        self.config = MakeConfig(pki, FreePort(), devicePort=FreePort())
        self.configFile = tmp_path / "config.toml"
        self.configFile.write_text(self.config.ToToml())
        self.received: list = []
        self.hub = HubServer(self.config, onMessage=self._OnHubMessage, link="device")
        self.devicePath = tmp_path / "holo-input"
        self.socketPath = tmp_path / "r.sock"
        self.modelsDir = tmp_path / "models"
        self.withDevice = withDevice
        self.startHub = startHub
        if withDevice and deviceFirst:
            os.mkfifo(self.devicePath)
        self.process: asyncio.subprocess.Process | None = None
        self.logs = ""

    async def _OnHubMessage(self, connection, message) -> None:
        self.received.append(message)

    async def StartAgent(self) -> None:
        command = [str(AGENT), "--config", str(self.configFile), "--name", "pi", "--models-dir", str(self.modelsDir), "--renderer-socket", str(self.socketPath), "--verbose"]
        command += ["--device", str(self.devicePath)] if self.withDevice else ["--no-device"]
        self.process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)

    async def __aenter__(self) -> "Harness":
        if self.startHub:
            await self.hub.Start()
        await self.StartAgent()
        if self.startHub:
            await WaitUntil(lambda: ("pi", Role.DEVICE_AGENT) in self.hub.peers, WAIT_S)
        return self

    async def __aexit__(self, *exc) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.send_signal(signal.SIGTERM)
        if self.process is not None:
            _, errors = await asyncio.wait_for(self.process.communicate(), WAIT_S)
            self.logs = errors.decode(errors="replace")
        if self.startHub:
            await self.hub.Stop()

    async def Renderer(self):
        await WaitUntil(self.socketPath.exists, WAIT_S)
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


def Inputs(harness: Harness) -> list:
    return [message for message in harness.received if isinstance(message, InputEvent)]


def test_button_events_reach_the_host_unchanged(pki, tmp_path):
    async def scenario():
        async with Harness(pki, tmp_path) as harness:
            writer = await harness.OpenDeviceWriter()
            events = [ButtonEvent(ButtonCode.RIGHT, EventValue.PRESS, 11), ButtonEvent(ButtonCode.RIGHT, EventValue.REPEAT, 12), ButtonEvent(ButtonCode.RIGHT, EventValue.RELEASE, 13)]
            os.write(writer, b"".join(EncodeHoloEvent(event) for event in events))
            await WaitUntil(lambda: len(Inputs(harness)) == 3, WAIT_S)
            os.close(writer)
            return events, Inputs(harness)

    events, received = asyncio.run(scenario())
    assert received == events


def test_a_record_split_across_two_reads_is_rebuilt(pki, tmp_path):
    async def scenario():
        async with Harness(pki, tmp_path) as harness:
            writer = await harness.OpenDeviceWriter()
            record = EncodeHoloEvent(ButtonEvent(ButtonCode.ZOOM_IN, EventValue.PRESS, 5))
            os.write(writer, record[:7])
            await asyncio.sleep(0.4)
            os.write(writer, record[7:])
            await WaitUntil(lambda: Inputs(harness), WAIT_S)
            os.close(writer)
            return Inputs(harness)

    assert asyncio.run(scenario()) == [ButtonEvent(ButtonCode.ZOOM_IN, EventValue.PRESS, 5)]


def test_a_burst_of_events_arrives_complete_and_in_order(pki, tmp_path):
    async def scenario():
        async with Harness(pki, tmp_path) as harness:
            writer = await harness.OpenDeviceWriter()
            events = [ButtonEvent(ButtonCode.LEFT, EventValue.REPEAT, index) for index in range(1, 201)]
            for start in range(0, len(events), 20):
                os.write(writer, b"".join(EncodeHoloEvent(event) for event in events[start : start + 20]))
                await asyncio.sleep(0.01)
            await WaitUntil(lambda: len(Inputs(harness)) == 200, WAIT_S)
            os.close(writer)
            return events, Inputs(harness)

    events, received = asyncio.run(scenario())
    assert received == events


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
    assert (first.yaw, first.pitch, first.zoom, first.modelId) == (10.0, 20.0, 1.5, "cup")
    assert second.yaw == 11.0
    assert replay.yaw == 11.0


def test_a_pushed_model_is_installed_and_the_state_is_repeated(pki, tmp_path):
    model = os.urandom(3000) + b"glTF" * 500

    async def scenario():
        async with Harness(pki, tmp_path, withDevice=False) as harness:
            reader, writer = await harness.Renderer()
            await harness.SendToAgent(CameraState(0.0, 20.0, 1.0, "bunny"))
            await ReadState(reader)
            for chunk in reversed(SplitModel("bunny", model, Compression.NONE, chunkBytes=1000)):
                await harness.SendToAgent(chunk)
            repeated = await ReadState(reader)
            writer.close()
            return repeated, (harness.modelsDir / "bunny.glb").read_bytes(), sorted(path.name for path in harness.modelsDir.iterdir())

    repeated, installed, files = asyncio.run(scenario())
    assert repeated.modelId == "bunny"
    assert installed == model
    assert files == ["bunny.glb"]


def test_a_model_larger_than_one_frame_is_installed(pki, tmp_path):
    model = os.urandom(2_500_000)

    async def scenario():
        async with Harness(pki, tmp_path, withDevice=False) as harness:
            for chunk in SplitModel("big", model, Compression.NONE, chunkBytes=1024 * 1024):
                await harness.SendToAgent(chunk)
            await WaitUntil(lambda: (harness.modelsDir / "big.glb").exists(), WAIT_S)
            return (harness.modelsDir / "big.glb").read_bytes()

    assert asyncio.run(scenario()) == model


def test_a_model_with_a_bad_hash_is_not_installed(pki, tmp_path):
    async def scenario():
        async with Harness(pki, tmp_path, withDevice=False) as harness:
            bad = ModelPush("bunny", 0, 1, 100, "0" * 64, Compression.NONE, b"x" * 100)
            await harness.SendToAgent(bad)
            for chunk in SplitModel("bunny", b"x" * 100):
                await harness.SendToAgent(chunk)
            await WaitUntil(lambda: (harness.modelsDir / "bunny.glb").exists(), WAIT_S)
            return (harness.modelsDir / "bunny.glb").read_bytes(), sorted(path.name for path in harness.modelsDir.iterdir())

    content, files = asyncio.run(scenario())
    assert content == b"x" * 100 and files == ["bunny.glb"]


def test_a_compressed_model_is_refused(pki, tmp_path):
    async def scenario():
        async with Harness(pki, tmp_path, withDevice=False) as harness:
            for chunk in SplitModel("packed", b"data", Compression.ZSTD):
                await harness.SendToAgent(chunk)
            for chunk in SplitModel("barrier", b"ok"):
                await harness.SendToAgent(chunk)
            await WaitUntil(lambda: (harness.modelsDir / "barrier.glb").exists(), WAIT_S)
            return sorted(path.name for path in harness.modelsDir.iterdir())

    assert asyncio.run(scenario()) == ["barrier.glb"]


@pytest.mark.parametrize("modelId", ["../escape", "a/b", ".hidden", "x" * 40, "with space", "ñandú"])
def test_models_with_unusable_ids_are_ignored(pki, tmp_path, modelId):
    async def scenario():
        async with Harness(pki, tmp_path, withDevice=False) as harness:
            for chunk in SplitModel(modelId, b"data"):
                await harness.SendToAgent(chunk)
            for chunk in SplitModel("barrier", b"ok"):
                await harness.SendToAgent(chunk)
            await WaitUntil(lambda: (harness.modelsDir / "barrier.glb").exists(), WAIT_S)
            return sorted(path.name for path in harness.modelsDir.rglob("*")), list(tmp_path.glob("*.glb"))

    inside, outside = asyncio.run(scenario())
    assert inside == ["barrier.glb"] and outside == []


def test_a_model_larger_than_the_limit_is_ignored(pki, tmp_path):
    async def scenario():
        async with Harness(pki, tmp_path, withDevice=False) as harness:
            huge = ModelPush("huge", 0, 1, 200 * 1024 * 1024, hashlib.sha256(b"").hexdigest(), Compression.NONE, b"")
            await harness.SendToAgent(huge)
            for chunk in SplitModel("barrier", b"ok"):
                await harness.SendToAgent(chunk)
            await WaitUntil(lambda: (harness.modelsDir / "barrier.glb").exists(), WAIT_S)
            return sorted(path.name for path in harness.modelsDir.iterdir())

    assert asyncio.run(scenario()) == ["barrier.glb"]


def test_events_are_dropped_while_the_host_is_unreachable(pki, tmp_path):
    async def scenario():
        harness = Harness(pki, tmp_path, startHub=False)
        async with harness:
            writer = await harness.OpenDeviceWriter()
            os.write(writer, EncodeHoloEvent(ButtonEvent(ButtonCode.LEFT, EventValue.PRESS)))
            await asyncio.sleep(0.5)
            os.close(writer)
        return harness.logs

    logs = asyncio.run(scenario())
    assert "1 button events dropped so far" in logs


def test_the_agent_waits_for_a_device_that_appears_later(pki, tmp_path):
    async def scenario():
        async with Harness(pki, tmp_path, deviceFirst=False) as harness:
            await asyncio.sleep(0.4)
            os.mkfifo(harness.devicePath)
            writer = await harness.OpenDeviceWriter()
            os.write(writer, EncodeHoloEvent(ButtonEvent(ButtonCode.AUTOROTATE, EventValue.PRESS, 9)))
            await WaitUntil(lambda: Inputs(harness), WAIT_S + 4)
            os.close(writer)
            return Inputs(harness)

    assert asyncio.run(scenario()) == [ButtonEvent(ButtonCode.AUTOROTATE, EventValue.PRESS, 9)]


def test_the_agent_reconnects_when_the_hub_restarts(pki, tmp_path):
    async def scenario():
        harness = Harness(pki, tmp_path, withDevice=False)
        async with harness:
            await harness.hub.Stop()
            harness.hub = HubServer(harness.config, onMessage=harness._OnHubMessage, link="device")
            await harness.hub.Start()
            await WaitUntil(lambda: ("pi", Role.DEVICE_AGENT) in harness.hub.peers, WAIT_S + 6)
            reader, writer = await harness.Renderer()
            await harness.SendToAgent(CameraState(3.0, 20.0, 1.0, "cup"))
            state = await ReadState(reader)
            writer.close()
            return state

    assert asyncio.run(scenario()).yaw == 3.0


def test_the_agent_stops_cleanly_on_sigterm_and_removes_its_socket(pki, tmp_path):
    async def scenario():
        harness = Harness(pki, tmp_path, withDevice=False)
        async with harness:
            await harness.Renderer()
        return harness.process.returncode, harness.socketPath.exists()

    code, socketLeft = asyncio.run(scenario())
    assert code == 0 and not socketLeft
