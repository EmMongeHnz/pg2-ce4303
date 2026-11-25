"""holo-event.h and astrea_common.holo_event describe the same record."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from astrea_common.holo_event import (
    HOLO_EVENT_STRUCT,
    ButtonCode,
    DecodeHoloEvents,
    EncodeHoloEvent,
    EventValue,
)
from astrea_common.messages import InputEvent, InputSource

HEADER = Path(__file__).resolve().parents[2] / "driver" / "holo-io" / "holo-event.h"


def HeaderDefines() -> dict[str, int]:
    return {name: int(value) for name, value in re.findall(r"^#define\s+(HOLO_\w+)\s+(\d+)\s*$", HEADER.read_text(), re.M)}


def test_sources_match_the_header():
    defines = HeaderDefines()
    assert defines["HOLO_SOURCE_JOYSTICK"] == InputSource.JOYSTICK
    assert defines["HOLO_SOURCE_ZX_GESTURE"] == InputSource.ZX_GESTURE


def test_button_codes_match_the_header():
    defines = HeaderDefines()
    headerCodes = {name.removeprefix("HOLO_CODE_BTN_"): value for name, value in defines.items() if name.startswith("HOLO_CODE_BTN_")}
    assert headerCodes == {code.name: code.value for code in ButtonCode}


def test_event_values_match_the_header():
    defines = HeaderDefines()
    headerValues = {name.removeprefix("HOLO_VALUE_"): value for name, value in defines.items() if name.startswith("HOLO_VALUE_")}
    assert headerValues == {value.name: value.value for value in EventValue}


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc is not installed")
def test_struct_layout_matches_the_header(tmp_path):
    probe = tmp_path / "probe.c"
    probe.write_text(
        '#include <stddef.h>\n#include <stdio.h>\n#include "holo-event.h"\n'
        "int main(void) {\n"
        '    printf("%zu %zu %zu %zu %zu\\n", sizeof(struct HoloEvent), offsetof(struct HoloEvent, timestampNs),\n'
        "           offsetof(struct HoloEvent, source), offsetof(struct HoloEvent, code), offsetof(struct HoloEvent, value));\n"
        "    return 0;\n}\n"
    )
    binary = tmp_path / "probe"
    subprocess.run(["gcc", "-std=c11", f"-I{HEADER.parent}", str(probe), "-o", str(binary)], check=True)
    sizes = subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout.split()
    assert [int(size) for size in sizes] == [HOLO_EVENT_STRUCT.size, 0, 8, 10, 12]


def test_round_trip_of_several_records():
    events = [
        InputEvent(InputSource.JOYSTICK, ButtonCode.LEFT, EventValue.PRESS, 123456789012),
        InputEvent(InputSource.JOYSTICK, ButtonCode.AUTOROTATE, EventValue.RELEASE, 123456789999),
    ]
    buffer = b"".join(EncodeHoloEvent(event) for event in events)
    assert len(buffer) == 32
    assert DecodeHoloEvents(buffer) == events


def test_truncated_buffer_is_rejected():
    with pytest.raises(ValueError):
        DecodeHoloEvents(b"\x00" * 15)
