"""Checks on the holo-io sources that need no kernel: the library and its reader, the timing ranges and the overlay."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from coordinator.camera_control import BUTTON_HOLD_TIMEOUT_S

DRIVER = Path(__file__).resolve().parents[2] / "driver" / "holo-io"
REPEATS_PER_HOLD_TIMEOUT = 3


def IoctlDefines() -> dict[str, int]:
    return {name: int(value) for name, value in re.findall(r"^#define\s+(HOLO_\w+_MS)\s+(\d+)\s*$", (DRIVER / "holo-ioctl.h").read_text(), re.M)}


def EventDefines() -> dict[str, int]:
    return {name: int(value) for name, value in re.findall(r"^#define\s+(HOLO_CODE_\w+)\s+(\d+)\s*$", (DRIVER / "holo-event.h").read_text(), re.M)}


def test_the_slowest_repeat_leaves_room_for_several_repeats_per_host_timeout():
    defines = IoctlDefines()
    assert defines["HOLO_REPEAT_MAX_MS"] * REPEATS_PER_HOLD_TIMEOUT <= BUTTON_HOLD_TIMEOUT_S * 1000


def test_timing_defaults_lie_inside_their_ranges():
    defines = IoctlDefines()
    for name in ("DEBOUNCE", "REPEAT"):
        assert defines[f"HOLO_{name}_MIN_MS"] <= defines[f"HOLO_{name}_DEFAULT_MS"] <= defines[f"HOLO_{name}_MAX_MS"]


def test_debounce_default_is_inside_the_planned_window():
    assert 5 <= IoctlDefines()["HOLO_DEBOUNCE_DEFAULT_MS"] <= 10


def test_overlay_declares_exactly_the_labels_the_driver_accepts():
    overlayLabels = re.findall(r'label\s*=\s*"([^"]+)"', (DRIVER / "holo-io-overlay.dts").read_text())
    driverLabels = re.findall(r'^\s*\{\s*"([^"]+)",\s*HOLO_CODE_', (DRIVER / "holo-buttons.c").read_text(), re.M)
    assert sorted(overlayLabels) == sorted(driverLabels)
    assert len(driverLabels) == len(EventDefines())


def test_overlay_uses_the_documented_pins():
    text = (DRIVER / "holo-io-overlay.dts").read_text()
    pinsByLabel = dict(re.findall(r'label\s*=\s*"([^"]+)";\s*gpios\s*=\s*<&gpio\s+(\d+)\s+1>', text))
    assert pinsByLabel == {"right": "4", "left": "27", "zoom-in": "22", "zoom-out": "23", "autorotate": "24"}
    assert re.search(r"brcm,pins\s*=\s*<4 27 22 23 24>", text)


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc is not installed")
def test_the_library_and_the_reader_compile_without_warnings(tmp_path):
    library = DRIVER.parents[1] / "libholo"
    subprocess.run(
        ["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", f"-I{library / 'include'}", f"-I{DRIVER}", "-o", str(tmp_path / "holo-reader"), str(library / "tools" / "holo-reader.c"), str(library / "src" / "holo.c")],
        check=True,
        capture_output=True,
    )
