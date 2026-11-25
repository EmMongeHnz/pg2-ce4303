"""HostService with a real DeviceAgentService over mutual TLS: buttons in, the same camera state out to both viewers."""

import asyncio
import os
import socket
import time
from pathlib import Path

from astrea_common.config import ClusterConfig, ParseConfig
from astrea_common.holo_event import ButtonCode, EncodeHoloEvent, EventValue
from astrea_common.messages import InputEvent, InputSource
from astrea_common.render_link import STATE_STRUCT, DecodeState
from coordinator.host_service import FindModelIds, HostService
from device_agent.service import DeviceAgentService

WAIT_S = 5.0
REPEAT_S = 0.05


def FreePort() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def MakeConfig(pki) -> ClusterConfig:
    table = {
        "hub": {"node": "host", "address": "127.0.0.1", "port": FreePort(), "device-port": FreePort()},
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


async def ReadState(reader: asyncio.StreamReader):
    return DecodeState(await asyncio.wait_for(reader.readexactly(STATE_STRUCT.size), timeout=WAIT_S))


async def ReadUntil(reader: asyncio.StreamReader, predicate, attempts: int = 600):
    for _ in range(attempts):
        state = await ReadState(reader)
        if predicate(state):
            return state
    raise AssertionError("the expected state never arrived")


class World:
    """A host service, a Pi agent with a FIFO instead of the driver, and a fake renderer on each side."""

    def __init__(self, pki, tmp_path: Path, models: dict[str, bytes]) -> None:
        self.config = MakeConfig(pki)
        self.hostModels = tmp_path / "host-models"
        self.hostModels.mkdir()
        for name, data in models.items():
            (self.hostModels / f"{name}.glb").write_bytes(data)
        self.piModels = tmp_path / "pi-models"
        self.hostSocket = tmp_path / "host.sock"
        self.piSocket = tmp_path / "pi.sock"
        self.device = tmp_path / "holo-input"
        os.mkfifo(self.device)
        self.host = HostService(self.config, self.hostModels, self.hostSocket)
        self.agent = DeviceAgentService(self.config, "pi", self.piSocket, self.piModels, self.device)
        self._runner: asyncio.Task | None = None

    async def __aenter__(self) -> "World":
        await self.host.Start()
        self._runner = asyncio.create_task(self.agent.Run())
        await self.agent.WaitConnected(WAIT_S)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.agent.Stop()
        await self._runner
        await self.host.Stop()

    async def Viewer(self, path: Path):
        await WaitUntil(path.exists)
        return await asyncio.open_unix_connection(str(path))

    async def OpenDevice(self) -> int:
        async def attempt():
            while True:
                try:
                    return os.open(self.device, os.O_WRONLY | os.O_NONBLOCK)
                except OSError:
                    await asyncio.sleep(0.02)

        return await asyncio.wait_for(attempt(), WAIT_S)

    async def HoldButton(self, descriptor: int, code: ButtonCode, seconds: float) -> None:
        def write(value: EventValue) -> None:
            os.write(descriptor, EncodeHoloEvent(InputEvent(InputSource.JOYSTICK, code, value, time.monotonic_ns())))

        write(EventValue.PRESS)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(REPEAT_S)
            write(EventValue.REPEAT)
        write(EventValue.RELEASE)


def test_a_held_button_turns_both_viewers_to_the_same_yaw(pki, tmp_path):
    async def scenario():
        async with World(pki, tmp_path, {}) as world:
            hostReader, hostWriter = await world.Viewer(world.hostSocket)
            piReader, piWriter = await world.Viewer(world.piSocket)
            descriptor = await world.OpenDevice()
            await world.HoldButton(descriptor, ButtonCode.RIGHT, 0.4)
            await WaitUntil(lambda: world.host.eventsReceived >= 3)
            await asyncio.sleep(0.6)
            final = world.host.control.Snapshot()
            hostState = await ReadUntil(hostReader, lambda state: abs(state.yaw - final.yaw) < 1e-3)
            piState = await ReadUntil(piReader, lambda state: abs(state.yaw - final.yaw) < 1e-3)
            os.close(descriptor)
            hostWriter.close()
            piWriter.close()
            return final, hostState, piState

    final, hostState, piState = asyncio.run(scenario())
    assert 20.0 < final.yaw < 120.0
    assert hostState.yaw == piState.yaw
    assert abs(hostState.yaw - final.yaw) < 1e-3


def test_keyboard_input_of_the_host_viewer_also_reaches_the_pi(pki, tmp_path):
    from astrea_common.render_link import EncodeInput, InputAction, InputKind, RendererInput

    async def scenario():
        async with World(pki, tmp_path, {}) as world:
            piReader, piWriter = await world.Viewer(world.piSocket)
            hostReader, hostWriter = await world.Viewer(world.hostSocket)
            hostWriter.write(EncodeInput(RendererInput(InputKind.ACTION_PRESS, InputAction.TOGGLE_AUTO_ROTATE, 0.0)))
            state = await ReadUntil(piReader, lambda state: state.yaw > 3.0)
            hostWriter.close()
            piWriter.close()
            return state

    assert asyncio.run(scenario()).yaw > 3.0


def test_zoom_and_auto_rotation_buttons_change_the_shared_state(pki, tmp_path):
    async def scenario():
        async with World(pki, tmp_path, {}) as world:
            piReader, piWriter = await world.Viewer(world.piSocket)
            descriptor = await world.OpenDevice()
            await world.HoldButton(descriptor, ButtonCode.ZOOM_IN, 0.3)
            zoomed = await ReadUntil(piReader, lambda state: state.zoom > 1.1)
            os.write(descriptor, EncodeHoloEvent(InputEvent(InputSource.JOYSTICK, ButtonCode.AUTOROTATE, EventValue.PRESS, 1)))
            os.write(descriptor, EncodeHoloEvent(InputEvent(InputSource.JOYSTICK, ButtonCode.AUTOROTATE, EventValue.RELEASE, 2)))
            await WaitUntil(lambda: world.host.control.autoRotate)
            os.close(descriptor)
            piWriter.close()
            return zoomed

    assert asyncio.run(scenario()).zoom > 1.1


def test_models_are_pushed_to_the_pi_when_its_agent_connects(pki, tmp_path):
    cup = os.urandom(5000) + b"glTF" * 1000
    bunny = b"bunny-model" * 3000

    async def scenario():
        async with World(pki, tmp_path, {"cup": cup, "bunny": bunny}) as world:
            piReader, piWriter = await world.Viewer(world.piSocket)
            await WaitUntil(lambda: world.agent.modelsInstalled == 2)
            state = await ReadUntil(piReader, lambda state: state.modelId == "bunny")
            piWriter.close()
            return state, {path.name: path.read_bytes() for path in world.piModels.iterdir()}, world.host.modelsPushed

    state, installed, pushed = asyncio.run(scenario())
    assert installed == {"cup.glb": cup, "bunny.glb": bunny}
    assert pushed == 2
    assert state.modelId == "bunny"


def test_next_model_changes_the_model_on_both_viewers(pki, tmp_path):
    from astrea_common.render_link import EncodeInput, InputAction, InputKind, RendererInput

    async def scenario():
        async with World(pki, tmp_path, {"a": b"1", "b": b"2"}) as world:
            hostReader, hostWriter = await world.Viewer(world.hostSocket)
            piReader, piWriter = await world.Viewer(world.piSocket)
            first = await ReadState(hostReader)
            hostWriter.write(EncodeInput(RendererInput(InputKind.ACTION_PRESS, InputAction.NEXT_MODEL, 0.0)))
            hostState = await ReadUntil(hostReader, lambda state: state.modelId == "b")
            piState = await ReadUntil(piReader, lambda state: state.modelId == "b")
            hostWriter.close()
            piWriter.close()
            return first, hostState, piState

    first, hostState, piState = asyncio.run(scenario())
    assert first.modelId == "a"
    assert hostState.modelId == piState.modelId == "b"


def test_losing_the_pi_while_a_button_is_held_stops_the_motion(pki, tmp_path):
    async def scenario():
        world = World(pki, tmp_path, {})
        async with world:
            descriptor = await world.OpenDevice()
            os.write(descriptor, EncodeHoloEvent(InputEvent(InputSource.JOYSTICK, ButtonCode.RIGHT, EventValue.PRESS, 1)))
            await WaitUntil(lambda: world.host.eventsReceived == 1)
            await world.agent.Stop()
            await WaitUntil(lambda: not world.host._Agents())
            await asyncio.sleep(0.1)
            frozen = world.host.control.Snapshot().yaw
            await asyncio.sleep(0.2)
            os.close(descriptor)
            return frozen, world.host.control.Snapshot().yaw

    frozen, later = asyncio.run(scenario())
    assert later == frozen


def test_find_model_ids_lists_only_usable_glb_files(tmp_path):
    for name in ["cup.glb", "b.glb", ".hidden.glb", "with space.glb", "notes.txt", "x" * 40 + ".glb"]:
        (tmp_path / name).write_bytes(b"")
    assert FindModelIds(tmp_path) == ("b", "cup")
    assert FindModelIds(tmp_path / "missing") == ()
