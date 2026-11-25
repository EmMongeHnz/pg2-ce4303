"""Python side of `renderer/src/control-protocol.h`: the fixed binary records of the renderer's Unix socket.

Both ends run on the same machine, so the records use the native byte order (little-endian on the Pi 3 and on x86).
"""

import struct
from dataclasses import dataclass
from enum import IntEnum

from astrea_common.messages import CameraState

MODEL_ID_BYTES = 32
STATE_STRUCT = struct.Struct(f"<fff{MODEL_ID_BYTES}s")
INPUT_STRUCT = struct.Struct("<BBHf")


class InputKind(IntEnum):
    ACTION_PRESS = 1
    ACTION_RELEASE = 2
    MOUSE_DELTA = 3


class InputAction(IntEnum):
    YAW_LEFT = 1
    YAW_RIGHT = 2
    ZOOM_IN = 3
    ZOOM_OUT = 4
    TOGGLE_AUTO_ROTATE = 5
    NEXT_MODEL = 6


@dataclass(frozen=True)
class RendererInput:
    kind: InputKind
    action: int
    value: float


def EncodeState(state: CameraState) -> bytes:
    modelId = state.modelId.encode("utf-8")
    if len(modelId) >= MODEL_ID_BYTES:
        raise ValueError(f"modelId must fit in {MODEL_ID_BYTES - 1} bytes, got {len(modelId)}")
    return STATE_STRUCT.pack(state.yaw, state.pitch, state.zoom, modelId)


def DecodeState(record: bytes) -> CameraState:
    yaw, pitch, zoom, rawModelId = STATE_STRUCT.unpack(record)
    return CameraState(yaw, pitch, zoom, rawModelId.split(b"\0", 1)[0].decode("utf-8"))


def EncodeInput(event: RendererInput) -> bytes:
    return INPUT_STRUCT.pack(event.kind, event.action, 0, event.value)


class InputDecoder:
    """Rebuilds records from a byte stream that can be cut anywhere."""

    def __init__(self) -> None:
        self._pending = b""

    def Feed(self, data: bytes) -> list[RendererInput]:
        self._pending += data
        events = []
        while len(self._pending) >= INPUT_STRUCT.size:
            record, self._pending = self._pending[: INPUT_STRUCT.size], self._pending[INPUT_STRUCT.size :]
            kind, action, _reserved, value = INPUT_STRUCT.unpack(record)
            events.append(RendererInput(InputKind(kind), action, value))
        return events
