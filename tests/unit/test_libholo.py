"""libholo, the library that talks to the driver: events, timeouts, record reassembly, timings, statistics and the rule
that nothing else touches the driver. A FIFO stands in for /dev/holo-input; the harness is built with the sanitizers."""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from astrea_common.holo_event import ButtonCode, EncodeHoloEvent, EventValue
from astrea_common.messages import InputEvent, InputSource

REPO = Path(__file__).resolve().parents[2]
LIBRARY = REPO / "libholo"
needsGcc = pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc is not installed")

HARNESS = r"""
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "holo.h"

static void PrintError(const char *what) { printf("%s error %s\n", what, strerror(errno)); }

int main(int argc, char **argv) {
    (void)argc;
    const char *mode = argv[1];
    if (strcmp(mode, "read") == 0) {
        HoloDevice *device;
        if (HoloOpen(argv[2], &device) != HOLO_OK) { PrintError("open"); return 0; }
        int timeoutMs = atoi(argv[3]), count = atoi(argv[4]);
        for (int i = 0; i < count; i++) {
            struct HoloEvent event;
            HoloStatus status = HoloReadEvent(device, timeoutMs, &event);
            if (status != HOLO_OK) { printf("%s\n", HoloStatusText(status)); break; }
            printf("%llu %s %s\n", (unsigned long long)event.timestampNs, HoloButtonName(event.code), HoloValueName(event.value));
            fflush(stdout);
        }
        HoloClose(device);
    } else if (strcmp(mode, "timing") == 0) {
        HoloDevice *device;
        if (HoloOpen(argv[2], &device) != HOLO_OK) { PrintError("open"); return 0; }
        unsigned value = 0;
        if (HoloGetDebounceMs(device, &value) != HOLO_OK) PrintError("get");
        if (HoloSetRepeatMs(device, 50) != HOLO_OK) PrintError("set");
        HoloClose(device);
    } else if (strcmp(mode, "stats") == 0) {
        HoloStats stats;
        if (HoloReadStats(argv[2], &stats) != HOLO_OK) { PrintError("stats"); return 0; }
        printf("%lu %lu %lu %lu %lu %lu %lu %lu %lu\n", stats.irqs, stats.bouncesFiltered, stats.presses, stats.releases, stats.repeats, stats.events, stats.dropped, stats.debounceMs, stats.repeatMs);
    } else if (strcmp(mode, "actions") == 0) {
        for (unsigned code = 0; code <= 6; code++)
            for (int value = 0; value <= 3; value++) {
                struct HoloEvent event = {.source = code == 6 ? HOLO_SOURCE_ZX_GESTURE : HOLO_SOURCE_JOYSTICK, .code = code == 6 ? 1 : code, .value = value};
                HoloAction action;
                HoloPhase phase;
                if (HoloActionOf(&event, &action, &phase)) printf("%u %d %s %d\n", code, value, HoloActionName(action), phase);
                else printf("%u %d none\n", code, value);
            }
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    if shutil.which("gcc") is None:
        pytest.skip("gcc is not installed")
    directory = tmp_path_factory.mktemp("libholo")
    source = directory / "harness.c"
    source.write_text(HARNESS)
    binary = directory / "harness"
    subprocess.run(
        ["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-omit-frame-pointer", f"-I{LIBRARY / 'include'}", f"-I{REPO / 'driver' / 'holo-io'}", str(source), str(LIBRARY / "src" / "holo.c"), "-o", str(binary)],
        check=True,
        capture_output=True,
    )
    return binary


def Event(code: ButtonCode, value: EventValue, timestampNs: int) -> bytes:
    return EncodeHoloEvent(InputEvent(InputSource.JOYSTICK, code, value, timestampNs))


def RunReader(harness, fifo: Path, timeoutMs: int, count: int) -> subprocess.Popen:
    return subprocess.Popen([str(harness), "read", str(fifo), str(timeoutMs), str(count)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def OpenWriter(fifo: Path) -> int:
    deadline = time.monotonic() + 5
    while True:
        try:
            return os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)


def test_events_come_out_in_order_with_their_names(harness, tmp_path):
    fifo = tmp_path / "device"
    os.mkfifo(fifo)
    reader = RunReader(harness, fifo, 2000, 3)
    writer = OpenWriter(fifo)
    os.write(writer, Event(ButtonCode.RIGHT, EventValue.PRESS, 11) + Event(ButtonCode.RIGHT, EventValue.REPEAT, 12) + Event(ButtonCode.RIGHT, EventValue.RELEASE, 13))
    output, errors = reader.communicate(timeout=10)
    os.close(writer)
    assert output.splitlines() == ["11 right press", "12 right repeat", "13 right release"]
    assert errors == "" and reader.returncode == 0


def test_a_record_cut_by_the_kernel_is_rebuilt(harness, tmp_path):
    fifo = tmp_path / "device"
    os.mkfifo(fifo)
    reader = RunReader(harness, fifo, 3000, 2)
    writer = OpenWriter(fifo)
    record = Event(ButtonCode.ZOOM_IN, EventValue.PRESS, 5)
    os.write(writer, record[:5])
    time.sleep(0.3)
    os.write(writer, record[5:] + Event(ButtonCode.ZOOM_OUT, EventValue.PRESS, 6)[:9])
    time.sleep(0.3)
    os.write(writer, Event(ButtonCode.ZOOM_OUT, EventValue.PRESS, 6)[9:])
    output, _ = reader.communicate(timeout=10)
    os.close(writer)
    assert output.splitlines() == ["5 zoom-in press", "6 zoom-out press"]


def test_waiting_without_events_times_out(harness, tmp_path):
    fifo = tmp_path / "device"
    os.mkfifo(fifo)
    reader = RunReader(harness, fifo, 150, 1)
    writer = OpenWriter(fifo)
    started = time.monotonic()
    output, _ = reader.communicate(timeout=10)
    os.close(writer)
    assert output.strip() == "timed out"
    assert time.monotonic() - started < 3


def test_the_end_of_the_device_is_reported_as_closed(harness, tmp_path):
    fifo = tmp_path / "device"
    os.mkfifo(fifo)
    reader = RunReader(harness, fifo, 3000, 1)
    writer = OpenWriter(fifo)
    time.sleep(0.2)
    os.close(writer)
    output, _ = reader.communicate(timeout=10)
    assert output.strip() == "the device was closed"


def test_opening_a_missing_device_fails_with_the_reason(harness, tmp_path):
    result = subprocess.run([str(harness), "read", str(tmp_path / "missing"), "100", "1"], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "open error No such file or directory"


def test_the_timing_ioctls_fail_cleanly_on_something_that_is_not_the_driver(harness, tmp_path):
    fifo = tmp_path / "device"
    os.mkfifo(fifo)
    keeper = os.open(fifo, os.O_RDWR)
    output = subprocess.run([str(harness), "timing", str(fifo)], capture_output=True, text=True, check=True, timeout=10).stdout
    os.close(keeper)
    assert output.splitlines() == ["get error Inappropriate ioctl for device", "set error Inappropriate ioctl for device"]


def test_the_statistics_are_read_from_the_driver_files(harness, tmp_path):
    values = {"irqs": 10, "bounces_filtered": 7, "presses": 2, "releases": 1, "repeats": 5, "events": 8, "dropped": 0, "debounce_ms": 10, "repeat_ms": 50}
    for name, value in values.items():
        (tmp_path / name).write_text(f"{value}\n")
    result = subprocess.run([str(harness), "stats", str(tmp_path)], capture_output=True, text=True, check=True)
    assert result.stdout.split() == [str(value) for value in values.values()]


def test_missing_or_garbled_statistics_are_errors(harness, tmp_path):
    missing = subprocess.run([str(harness), "stats", str(tmp_path)], capture_output=True, text=True, check=True)
    assert missing.stdout.strip() == "stats error No such file or directory"
    for name in ["irqs", "bounces_filtered", "presses", "releases", "repeats", "events", "dropped", "debounce_ms", "repeat_ms"]:
        (tmp_path / name).write_text("not a number\n")
    garbled = subprocess.run([str(harness), "stats", str(tmp_path)], capture_output=True, text=True, check=True)
    assert garbled.stdout.strip() == "stats error Invalid argument"


def test_buttons_translate_into_actions_and_phases(harness):
    lines = subprocess.run([str(harness), "actions"], capture_output=True, text=True, check=True).stdout.splitlines()
    table = {tuple(line.split()[:2]): line.split()[2:] for line in lines}
    assert table[("1", "1")] == ["MoveRight", "1"]
    assert table[("2", "2")] == ["MoveLeft", "2"]
    assert table[("3", "0")] == ["ZoomIn", "0"]
    assert table[("4", "1")] == ["ZoomOut", "1"]
    assert table[("5", "1")] == ["ToggleAutoRotate", "1"]
    assert table[("0", "1")] == ["none"] and table[("6", "1")] == ["none"] and table[("1", "3")] == ["none"]


@needsGcc
def test_the_static_library_is_built_with_the_public_functions():
    subprocess.run(["make", "-C", str(LIBRARY)], check=True, capture_output=True)
    archive = LIBRARY / "build" / "libholo.a"
    assert archive.exists()
    symbols = subprocess.run(["nm", "-g", str(archive)], check=True, capture_output=True, text=True).stdout
    for name in ["HoloOpen", "HoloClose", "HoloReadEvent", "HoloGetDebounceMs", "HoloSetDebounceMs", "HoloGetRepeatMs", "HoloSetRepeatMs", "HoloReadStats", "HoloActionOf"]:
        assert re.search(rf" T {name}$", symbols, re.M), name


def test_nothing_but_the_library_talks_to_the_driver():
    allowed = {LIBRARY, REPO / "driver", REPO / "tests", REPO / "docs", REPO / "plan", REPO / ".venv", REPO / "out", REPO / "net" / "build", REPO / "net" / "build-san"}
    offenders = []
    for path in list(REPO.rglob("*.c")) + list(REPO.rglob("*.h")) + list(REPO.rglob("*.py")):
        if any(parent in allowed for parent in path.parents) or ".venv" in path.parts or "build" in path.parts:
            continue
        text = path.read_text(errors="ignore")
        if "HOLO_IOC_" in text or "holo-ioctl.h" in text or "/sys/class/misc/holo" in text:
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []
