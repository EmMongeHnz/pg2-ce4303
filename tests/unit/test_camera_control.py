"""CameraControl: mixing of keyboard, mouse and GPIO buttons, hold timeouts, auto-rotation and emission rate."""

import re
from pathlib import Path

import pytest

from astrea_common.holo_event import ButtonCode, EventValue
from astrea_common.messages import InputEvent, InputSource
from coordinator import camera_control as cc
from coordinator.camera_control import CameraAction, CameraControl, InputDevice

RENDERER_SOURCES = Path(__file__).resolve().parents[2] / "renderer" / "src"


def ButtonEvent(code: ButtonCode, value: EventValue) -> InputEvent:
    return InputEvent(InputSource.JOYSTICK, code, value, 0)


def RendererDefines() -> dict[str, float]:
    text = "\n".join(path.read_text() for path in (RENDERER_SOURCES / "orbit-camera.c", RENDERER_SOURCES / "options.c"))
    return {name: float(value) for name, value in re.findall(r"^#define\s+(\w+)\s+(-?[\d.]+)f\s*$", text, re.M)}


def test_constants_mirror_the_renderer():
    defines = RendererDefines()
    mirrored = {
        "DEFAULT_PITCH_DEG": cc.PITCH_DEG,
        "MIN_ZOOM": cc.MIN_ZOOM,
        "MAX_ZOOM": cc.MAX_ZOOM,
        "KEY_YAW_DEG_PER_S": cc.KEY_YAW_DEG_PER_S,
        "KEY_ZOOM_PER_S": cc.KEY_ZOOM_PER_S,
        "MOUSE_DEG_PER_PX": cc.MOUSE_DEG_PER_PX,
        "HOLOGRAM_AUTO_ROTATE_DEG_PER_S": cc.AUTO_ROTATE_DEG_PER_S,
    }
    for name, value in mirrored.items():
        assert value == pytest.approx(defines[name]), name


def test_initial_state_is_the_default_view_without_auto_rotation():
    control = CameraControl(("cup",))
    state = control.Snapshot()
    assert (state.yaw, state.pitch, state.zoom, state.modelId) == (0.0, 20.0, 1.0, "cup")
    assert not control.autoRotate


def test_the_pitch_never_changes():
    control = CameraControl()
    control.PressKey(CameraAction.YAW_RIGHT, 0.0)
    control.ApplyMouseDelta(50.0, 0.5)
    control.PollEmission(3.0)
    assert control.Snapshot().pitch == 20.0


def test_held_key_turns_at_the_renderer_rate():
    control = CameraControl()
    control.PressKey(CameraAction.YAW_RIGHT, 0.0)
    control.ReleaseKey(CameraAction.YAW_RIGHT, 0.5)
    assert control.Snapshot().yaw == pytest.approx(45.0)


def test_yaw_wraps_below_zero():
    control = CameraControl()
    control.PressKey(CameraAction.YAW_LEFT, 0.0)
    control.ReleaseKey(CameraAction.YAW_LEFT, 1.0)
    assert control.Snapshot().yaw == pytest.approx(270.0)


def test_opposite_keys_cancel():
    control = CameraControl()
    control.PressKey(CameraAction.YAW_LEFT, 0.0)
    control.PressKey(CameraAction.YAW_RIGHT, 0.0)
    control.PollEmission(1.0)
    assert control.Snapshot().yaw == pytest.approx(0.0)


def test_zoom_is_clamped():
    control = CameraControl()
    control.PressKey(CameraAction.ZOOM_IN, 0.0)
    control.PollEmission(30.0)
    assert control.Snapshot().zoom == cc.MAX_ZOOM
    control.ReleaseKey(CameraAction.ZOOM_IN, 30.0)
    control.PressKey(CameraAction.ZOOM_OUT, 30.0)
    control.PollEmission(60.0)
    assert control.Snapshot().zoom == cc.MIN_ZOOM


def test_zoom_in_then_out_returns_to_the_start():
    control = CameraControl()
    control.PressKey(CameraAction.ZOOM_IN, 0.0)
    control.ReleaseKey(CameraAction.ZOOM_IN, 0.4)
    control.PressKey(CameraAction.ZOOM_OUT, 0.4)
    control.ReleaseKey(CameraAction.ZOOM_OUT, 0.8)
    assert control.Snapshot().zoom == pytest.approx(1.0)


def test_keyboard_and_buttons_in_the_same_direction_add_up():
    control = CameraControl()
    control.PressKey(CameraAction.YAW_RIGHT, 0.0)
    control.HandleButtonEvent(ButtonEvent(ButtonCode.RIGHT, EventValue.PRESS), 0.0)
    control.PollEmission(0.2)
    assert control.Snapshot().yaw == pytest.approx(2 * 90.0 * 0.2)


def test_button_hold_stops_when_the_repeats_stop():
    control = CameraControl(buttonHoldTimeoutS=0.3)
    control.HandleButtonEvent(ButtonEvent(ButtonCode.RIGHT, EventValue.PRESS), 0.0)
    control.PollEmission(5.0)
    assert control.Snapshot().yaw == pytest.approx(90.0 * 0.3)


def test_button_repeats_extend_the_hold():
    control = CameraControl(buttonHoldTimeoutS=0.3)
    control.HandleButtonEvent(ButtonEvent(ButtonCode.RIGHT, EventValue.PRESS), 0.0)
    control.HandleButtonEvent(ButtonEvent(ButtonCode.RIGHT, EventValue.REPEAT), 0.25)
    control.HandleButtonEvent(ButtonEvent(ButtonCode.RIGHT, EventValue.REPEAT), 0.5)
    control.PollEmission(10.0)
    assert control.Snapshot().yaw == pytest.approx(90.0 * 0.8)


def test_button_release_stops_immediately():
    control = CameraControl()
    control.HandleButtonEvent(ButtonEvent(ButtonCode.LEFT, EventValue.PRESS), 0.0)
    control.HandleButtonEvent(ButtonEvent(ButtonCode.LEFT, EventValue.RELEASE), 0.1)
    control.PollEmission(1.0)
    assert control.Snapshot().yaw == pytest.approx((-90.0 * 0.1) % 360.0)


def test_a_repeat_without_a_press_starts_the_motion():
    control = CameraControl(buttonHoldTimeoutS=0.3)
    control.HandleButtonEvent(ButtonEvent(ButtonCode.ZOOM_IN, EventValue.REPEAT), 0.0)
    control.PollEmission(1.0)
    assert control.Snapshot().zoom > 1.0


def test_auto_rotation_turns_at_the_renderer_rate_once_enabled():
    control = CameraControl()
    control.PollEmission(10.0)
    assert control.Snapshot().yaw == 0.0
    control.HandleButtonEvent(ButtonEvent(ButtonCode.AUTOROTATE, EventValue.PRESS), 10.0)
    control.PollEmission(11.0)
    assert control.autoRotate
    assert control.Snapshot().yaw == pytest.approx(20.0)


def test_auto_rotation_toggles_off_with_the_button_and_the_r_key():
    control = CameraControl()
    control.HandleButtonEvent(ButtonEvent(ButtonCode.AUTOROTATE, EventValue.PRESS), 0.0)
    control.HandleButtonEvent(ButtonEvent(ButtonCode.AUTOROTATE, EventValue.RELEASE), 0.1)
    assert control.autoRotate
    control.PressKey(CameraAction.TOGGLE_AUTO_ROTATE, 0.5)
    assert not control.autoRotate
    control.PollEmission(5.0)
    assert control.Snapshot().yaw == pytest.approx(20.0 * 0.5)


def test_a_held_autorotate_button_toggles_only_once():
    control = CameraControl()
    control.HandleButtonEvent(ButtonEvent(ButtonCode.AUTOROTATE, EventValue.PRESS), 0.0)
    control.HandleButtonEvent(ButtonEvent(ButtonCode.AUTOROTATE, EventValue.REPEAT), 0.1)
    assert control.autoRotate


def test_manual_yaw_adds_to_the_auto_rotation():
    control = CameraControl()
    control.PressKey(CameraAction.TOGGLE_AUTO_ROTATE, 0.0)
    control.PressKey(CameraAction.YAW_RIGHT, 0.0)
    control.PollEmission(1.0)
    assert control.Snapshot().yaw == pytest.approx(20.0 + 90.0)


def test_next_model_wraps():
    control = CameraControl(("a", "b"))
    control.PressKey(CameraAction.NEXT_MODEL, 0.0)
    assert control.Snapshot().modelId == "b"
    control.PressKey(CameraAction.NEXT_MODEL, 0.0)
    assert control.Snapshot().modelId == "a"


def test_next_model_without_models_keeps_an_empty_id():
    control = CameraControl()
    control.PressKey(CameraAction.NEXT_MODEL, 0.0)
    assert control.Snapshot().modelId == ""


def test_set_models_keeps_the_current_model_when_it_remains():
    control = CameraControl(("a", "b"))
    control.PressKey(CameraAction.NEXT_MODEL, 0.0)
    control.SetModels(("c", "b", "a"))
    assert control.Snapshot().modelId == "b"


def test_mouse_drag_turns_only_horizontally_like_the_renderer():
    control = CameraControl()
    control.ApplyMouseDelta(100.0, 0.0)
    assert control.Snapshot().yaw == pytest.approx((-100.0 * cc.MOUSE_DEG_PER_PX) % 360.0)


def test_releasing_a_device_stops_its_motion_only():
    control = CameraControl()
    control.PressKey(CameraAction.YAW_RIGHT, 0.0)
    control.HandleButtonEvent(ButtonEvent(ButtonCode.RIGHT, EventValue.PRESS), 0.0)
    control.ReleaseDevice(InputDevice.BUTTONS, 0.1)
    control.PollEmission(1.1)
    assert control.Snapshot().yaw == pytest.approx(2 * 90.0 * 0.1 + 90.0 * 1.0)


def test_unknown_button_input_is_ignored():
    control = CameraControl()
    assert not control.HandleButtonEvent(InputEvent(InputSource.ZX_GESTURE, 1, 1, 0), 0.0)
    assert not control.HandleButtonEvent(InputEvent(InputSource.JOYSTICK, 99, 1, 0), 0.0)
    assert not control.HandleButtonEvent(InputEvent(InputSource.JOYSTICK, 1, 7, 0), 0.0)


def test_emission_is_limited_and_the_final_state_is_always_sent():
    control = CameraControl()
    control.PollEmission(0.0)
    control.PressKey(CameraAction.YAW_RIGHT, 0.0)
    emitted = []
    for step in range(1, 1001):
        state = control.PollEmission(step / 1000.0)
        if state is not None:
            emitted.append(state)
    control.ReleaseKey(CameraAction.YAW_RIGHT, 1.0)
    final = control.PollEmission(1.1)
    assert 55 <= len(emitted) <= 61
    assert final is not None and final.yaw == pytest.approx(90.0)


def test_nothing_is_emitted_while_idle():
    control = CameraControl()
    assert control.PollEmission(0.0) is not None
    assert control.PollEmission(1.0) is None
    assert control.PollEmission(2.0) is None
