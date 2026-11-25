"""RendererBridge: a fake renderer on the Unix socket drives CameraControl and receives the camera state."""

import asyncio
import time

import pytest

from astrea_common.render_link import (
    STATE_STRUCT,
    DecodeState,
    EncodeInput,
    InputAction,
    InputKind,
    RendererInput,
)
from coordinator.camera_control import CameraAction, CameraControl
from coordinator.renderer_bridge import RendererBridge

TIMEOUT_S = 2.0


def Press(action: InputAction) -> bytes:
    return EncodeInput(RendererInput(InputKind.ACTION_PRESS, action, 0.0))


def Release(action: InputAction) -> bytes:
    return EncodeInput(RendererInput(InputKind.ACTION_RELEASE, action, 0.0))


async def ReadState(reader: asyncio.StreamReader):
    return DecodeState(await asyncio.wait_for(reader.readexactly(STATE_STRUCT.size), timeout=TIMEOUT_S))


async def ReadUntil(reader: asyncio.StreamReader, predicate, attempts: int = 400):
    for _ in range(attempts):
        state = await ReadState(reader)
        if predicate(state):
            return state
    raise AssertionError("the expected state never arrived")


def RunWithBridge(tmp_path, scenario):
    async def main():
        control = CameraControl(("cup", "bunny"), startS=time.monotonic())
        bridge = RendererBridge(control, tmp_path / "viewer.sock", tickIntervalS=0.005)
        await bridge.Start()
        try:
            return await scenario(bridge, control, str(tmp_path / "viewer.sock"))
        finally:
            await bridge.Stop()

    return asyncio.run(main())


def test_a_new_renderer_receives_the_current_state(tmp_path):
    async def scenario(bridge, control, path):
        reader, writer = await asyncio.open_unix_connection(path)
        state = await ReadState(reader)
        writer.close()
        return state

    state = RunWithBridge(tmp_path, scenario)
    assert (state.yaw, state.pitch, state.zoom, state.modelId) == (0.0, 20.0, 1.0, "cup")


def test_a_held_key_turns_the_camera_and_the_state_comes_back(tmp_path):
    async def scenario(bridge, control, path):
        reader, writer = await asyncio.open_unix_connection(path)
        writer.write(Press(InputAction.YAW_RIGHT))
        turning = await ReadUntil(reader, lambda state: state.yaw > 5.0)
        writer.write(Release(InputAction.YAW_RIGHT))
        await asyncio.sleep(0.1)
        frozen = control.Snapshot().yaw
        await asyncio.sleep(0.1)
        writer.close()
        return turning, frozen, control.Snapshot().yaw

    turning, frozen, later = RunWithBridge(tmp_path, scenario)
    assert turning.yaw > 5.0
    assert later == frozen


def test_next_model_and_auto_rotation_inputs_are_applied(tmp_path):
    async def scenario(bridge, control, path):
        reader, writer = await asyncio.open_unix_connection(path)
        await ReadState(reader)
        writer.write(Press(InputAction.NEXT_MODEL))
        model = await ReadUntil(reader, lambda state: state.modelId == "bunny")
        writer.write(Press(InputAction.TOGGLE_AUTO_ROTATE))
        rotated = await ReadUntil(reader, lambda state: state.yaw > 1.0)
        writer.close()
        return model, rotated, control.autoRotate

    model, rotated, autoRotate = RunWithBridge(tmp_path, scenario)
    assert model.modelId == "bunny"
    assert rotated.yaw > 1.0
    assert autoRotate


def test_mouse_movement_turns_the_camera(tmp_path):
    async def scenario(bridge, control, path):
        reader, writer = await asyncio.open_unix_connection(path)
        await ReadState(reader)
        writer.write(EncodeInput(RendererInput(InputKind.MOUSE_DELTA, 0, 100.0)))
        state = await ReadUntil(reader, lambda state: state.yaw != 0.0)
        writer.close()
        return state

    assert RunWithBridge(tmp_path, scenario).yaw == pytest.approx((-100.0 * 0.3) % 360.0)


def test_closing_the_renderer_releases_its_held_keys(tmp_path):
    async def scenario(bridge, control, path):
        reader, writer = await asyncio.open_unix_connection(path)
        await ReadState(reader)
        writer.write(Press(InputAction.YAW_RIGHT))
        await ReadUntil(reader, lambda state: state.yaw > 1.0)
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.15)
        frozen = control.Snapshot().yaw
        await asyncio.sleep(0.15)
        return frozen, control.Snapshot().yaw

    frozen, later = RunWithBridge(tmp_path, scenario)
    assert frozen > 1.0
    assert later == frozen


def test_state_listeners_receive_the_emissions(tmp_path):
    async def scenario(bridge, control, path):
        seen = []
        bridge.AddStateListener(seen.append)
        control.PressKey(CameraAction.YAW_RIGHT, time.monotonic())
        await asyncio.sleep(0.15)
        return seen

    seen = RunWithBridge(tmp_path, scenario)
    assert seen and seen[-1].yaw > 0.0


def test_a_second_renderer_replaces_the_first(tmp_path):
    async def scenario(bridge, control, path):
        firstReader, firstWriter = await asyncio.open_unix_connection(path)
        await ReadState(firstReader)
        secondReader, secondWriter = await asyncio.open_unix_connection(path)
        await ReadState(secondReader)
        firstEnded = await asyncio.wait_for(firstReader.read(1), timeout=TIMEOUT_S)
        secondWriter.close()
        firstWriter.close()
        return firstEnded

    assert RunWithBridge(tmp_path, scenario) == b""


def test_an_unknown_action_is_ignored(tmp_path):
    async def scenario(bridge, control, path):
        reader, writer = await asyncio.open_unix_connection(path)
        await ReadState(reader)
        writer.write(EncodeInput(RendererInput(InputKind.ACTION_PRESS, 99, 0.0)))
        writer.write(Press(InputAction.NEXT_MODEL))
        state = await ReadUntil(reader, lambda state: state.modelId == "bunny")
        writer.close()
        return state

    assert RunWithBridge(tmp_path, scenario).modelId == "bunny"
