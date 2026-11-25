"""Python side of `driver/holo-io/holo-event.h`: the record read from /dev/holo-input.

The device delivers native-endian records (little-endian on the Pi 3 and on x86), 16 bytes each.
"""

import struct
from enum import IntEnum

from astrea_common.messages import InputEvent

HOLO_EVENT_STRUCT = struct.Struct("<QHHi")


class ButtonCode(IntEnum):
    RIGHT = 1
    LEFT = 2
    ZOOM_IN = 3
    ZOOM_OUT = 4
    AUTOROTATE = 5


class EventValue(IntEnum):
    RELEASE = 0
    PRESS = 1
    REPEAT = 2


def DescribeEvent(event: InputEvent) -> str:
    try:
        code = ButtonCode(event.code).name.lower()
    except ValueError:
        code = f"code {event.code}"
    try:
        value = EventValue(event.value).name.lower()
    except ValueError:
        value = f"value {event.value}"
    return f"{code} {value}"


def DecodeHoloEvents(buffer: bytes) -> list[InputEvent]:
    if len(buffer) % HOLO_EVENT_STRUCT.size != 0:
        raise ValueError(f"expected a multiple of {HOLO_EVENT_STRUCT.size} bytes, got {len(buffer)}")
    return [
        InputEvent(source=source, code=code, value=value, timestampNs=timestampNs)
        for timestampNs, source, code, value in HOLO_EVENT_STRUCT.iter_unpack(buffer)
    ]


def EncodeHoloEvent(event: InputEvent) -> bytes:
    return HOLO_EVENT_STRUCT.pack(event.timestampNs, event.source, event.code, event.value)
