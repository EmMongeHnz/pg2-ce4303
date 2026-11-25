"""Host camera state: merges keyboard, mouse and GPIO button input into the one CameraState both viewers show.

The class does no I/O and reads no clock: callers pass the time of every call, so the behaviour is
deterministic. Contributions from all input devices add up, and the automatic rotation adds to the manual
yaw. The constants mirror `renderer/src/orbit-camera.c` and `renderer/src/options.c`;
tests/unit/test_camera_control.py keeps them equal.
"""

import math
from enum import Enum, auto

from astrea_common.holo_event import ButtonCode, EventValue
from astrea_common.messages import CameraState, InputEvent, InputSource

DEFAULT_YAW_DEG = 0.0
PITCH_DEG = 20.0
DEFAULT_ZOOM = 1.0
MIN_ZOOM = 0.4
MAX_ZOOM = 3.0
KEY_YAW_DEG_PER_S = 90.0
KEY_ZOOM_PER_S = 1.0
MOUSE_DEG_PER_PX = 0.3
AUTO_ROTATE_DEG_PER_S = 20.0

MAX_EMIT_HZ = 60.0

# The driver repeats a held button every few milliseconds; the hold ends when the repeats stop,
# so a lost release event cannot leave the camera moving. Must exceed the driver's repeat period
# (set in M1.5) by a safe margin.
BUTTON_HOLD_TIMEOUT_S = 0.3


class CameraAction(Enum):
    YAW_LEFT = auto()
    YAW_RIGHT = auto()
    ZOOM_IN = auto()
    ZOOM_OUT = auto()
    TOGGLE_AUTO_ROTATE = auto()
    NEXT_MODEL = auto()


class InputDevice(Enum):
    KEYBOARD = auto()
    BUTTONS = auto()


CONTINUOUS_ACTIONS = frozenset(
    {
        CameraAction.YAW_LEFT,
        CameraAction.YAW_RIGHT,
        CameraAction.ZOOM_IN,
        CameraAction.ZOOM_OUT,
    }
)

BUTTON_ACTIONS = {
    ButtonCode.RIGHT: CameraAction.YAW_RIGHT,
    ButtonCode.LEFT: CameraAction.YAW_LEFT,
    ButtonCode.ZOOM_IN: CameraAction.ZOOM_IN,
    ButtonCode.ZOOM_OUT: CameraAction.ZOOM_OUT,
    ButtonCode.AUTOROTATE: CameraAction.TOGGLE_AUTO_ROTATE,
}


def _ClampZoom(zoom: float) -> float:
    return min(max(zoom, MIN_ZOOM), MAX_ZOOM)


class CameraControl:
    def __init__(
        self,
        modelIds: tuple[str, ...] = (),
        startS: float = 0.0,
        buttonHoldTimeoutS: float = BUTTON_HOLD_TIMEOUT_S,
        maxEmitHz: float = MAX_EMIT_HZ,
    ) -> None:
        self._yawDeg = DEFAULT_YAW_DEG
        self._zoom = DEFAULT_ZOOM
        self._autoRotate = False
        self._modelIds = tuple(modelIds)
        self._modelIndex = 0
        self._buttonHoldTimeoutS = buttonHoldTimeoutS
        self._minEmitIntervalS = 1.0 / maxEmitHz
        self._heldUntilS: dict[tuple[InputDevice, CameraAction], float | None] = {}
        self._lastUpdateS = startS
        self._lastEmitS: float | None = None
        self._changedSinceEmit = True

    @property
    def autoRotate(self) -> bool:
        return self._autoRotate

    def Snapshot(self) -> CameraState:
        modelId = self._modelIds[self._modelIndex] if self._modelIds else ""
        return CameraState(self._yawDeg, PITCH_DEG, self._zoom, modelId)

    def PollEmission(self, nowS: float) -> CameraState | None:
        self._AdvanceTo(nowS)
        if not self._changedSinceEmit:
            return None
        if self._lastEmitS is not None and nowS - self._lastEmitS < self._minEmitIntervalS:
            return None
        self._lastEmitS = nowS
        self._changedSinceEmit = False
        return self.Snapshot()

    def SetModels(self, modelIds: tuple[str, ...]) -> None:
        currentId = self.Snapshot().modelId
        self._modelIds = tuple(modelIds)
        self._modelIndex = self._modelIds.index(currentId) if currentId in self._modelIds else 0
        self._changedSinceEmit = True

    def PressKey(self, action: CameraAction, nowS: float) -> None:
        self._AdvanceTo(nowS)
        if action in CONTINUOUS_ACTIONS:
            self._heldUntilS[(InputDevice.KEYBOARD, action)] = None
        else:
            self._Trigger(action)

    def ReleaseKey(self, action: CameraAction, nowS: float) -> None:
        self._AdvanceTo(nowS)
        self._heldUntilS.pop((InputDevice.KEYBOARD, action), None)

    def ApplyMouseDelta(self, deltaXPx: float, nowS: float) -> None:
        self._AdvanceTo(nowS)
        self._SetOrbit(self._yawDeg - deltaXPx * MOUSE_DEG_PER_PX, self._zoom)

    def HandleButtonEvent(self, event: InputEvent, nowS: float) -> bool:
        action = self._ButtonAction(event)
        if action is None:
            return False
        self._AdvanceTo(nowS)
        key = (InputDevice.BUTTONS, action)
        if event.value == EventValue.RELEASE:
            self._heldUntilS.pop(key, None)
        elif action in CONTINUOUS_ACTIONS:
            self._heldUntilS[key] = nowS + self._buttonHoldTimeoutS
        elif event.value == EventValue.PRESS:
            self._Trigger(action)
        return True

    def ReleaseDevice(self, device: InputDevice, nowS: float) -> None:
        self._AdvanceTo(nowS)
        for key in [key for key in self._heldUntilS if key[0] == device]:
            del self._heldUntilS[key]

    def _ButtonAction(self, event: InputEvent) -> CameraAction | None:
        if event.source != InputSource.JOYSTICK or event.value not in tuple(EventValue):
            return None
        try:
            return BUTTON_ACTIONS[ButtonCode(event.code)]
        except (ValueError, KeyError):
            return None

    def _Trigger(self, action: CameraAction) -> None:
        if action == CameraAction.TOGGLE_AUTO_ROTATE:
            self._autoRotate = not self._autoRotate
        elif action == CameraAction.NEXT_MODEL and self._modelIds:
            self._modelIndex = (self._modelIndex + 1) % len(self._modelIds)
            self._changedSinceEmit = True

    def _SetOrbit(self, yawDeg: float, zoom: float) -> None:
        orbit = (yawDeg % 360.0, _ClampZoom(zoom))
        if orbit != (self._yawDeg, self._zoom):
            self._yawDeg, self._zoom = orbit
            self._changedSinceEmit = True

    def _AdvanceTo(self, nowS: float) -> None:
        cursorS = self._lastUpdateS
        while cursorS < nowS:
            self._DropExpiredHolds(cursorS)
            segmentEndS = min(nowS, self._NextDeadlineS())
            self._Integrate(segmentEndS - cursorS)
            cursorS = segmentEndS
        self._lastUpdateS = max(self._lastUpdateS, nowS)

    def _DropExpiredHolds(self, atS: float) -> None:
        for key, deadlineS in list(self._heldUntilS.items()):
            if deadlineS is not None and deadlineS <= atS:
                del self._heldUntilS[key]

    def _NextDeadlineS(self) -> float:
        deadlines = [deadlineS for deadlineS in self._heldUntilS.values() if deadlineS is not None]
        return min(deadlines, default=math.inf)

    def _HeldCount(self, action: CameraAction) -> int:
        return sum(1 for (_, heldAction) in self._heldUntilS if heldAction == action)

    def _Integrate(self, durationS: float) -> None:
        yawRate = self._HeldCount(CameraAction.YAW_RIGHT) - self._HeldCount(CameraAction.YAW_LEFT)
        zoomRate = self._HeldCount(CameraAction.ZOOM_IN) - self._HeldCount(CameraAction.ZOOM_OUT)
        autoYawDegPerS = AUTO_ROTATE_DEG_PER_S if self._autoRotate else 0.0
        self._SetOrbit(
            self._yawDeg + (yawRate * KEY_YAW_DEG_PER_S + autoYawDegPerS) * durationS,
            self._zoom * (1.0 + KEY_ZOOM_PER_S * durationS) ** zoomRate,
        )
