"""control-protocol.h and astrea_common.render_link describe the same records; the C link talks to Python."""

import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from astrea_common.messages import CameraState
from astrea_common.render_link import (
    INPUT_STRUCT,
    MODEL_ID_BYTES,
    STATE_STRUCT,
    DecodeState,
    EncodeInput,
    EncodeState,
    InputAction,
    InputDecoder,
    InputKind,
    RendererInput,
)

RENDERER_SRC = Path(__file__).resolve().parents[2] / "renderer" / "src"
PROTOCOL_HEADER = RENDERER_SRC / "control-protocol.h"
needsGcc = pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc is not installed")


def HeaderDefines(prefix: str) -> dict[str, int]:
    pairs = re.findall(rf"^#define\s+CONTROL_{prefix}(\w+)\s+(\d+)\s*$", PROTOCOL_HEADER.read_text(), re.M)
    return {name: int(value) for name, value in pairs}


def test_kinds_match_the_header():
    assert HeaderDefines("KIND_") == {kind.name: kind.value for kind in InputKind}


def test_actions_match_the_header():
    assert HeaderDefines("ACTION_") == {action.name: action.value for action in InputAction}


def test_model_id_size_matches_the_header():
    assert HeaderDefines("MODEL_ID_")["BYTES"] == MODEL_ID_BYTES


@needsGcc
def test_record_layout_matches_the_header(tmp_path):
    probe = tmp_path / "probe.c"
    probe.write_text(
        '#include <stddef.h>\n#include <stdio.h>\n#include "control-protocol.h"\n'
        "int main(void) {\n"
        '    printf("%zu %zu %zu %zu %zu %zu %zu\\n", sizeof(ControlState), offsetof(ControlState, pitchDeg),\n'
        "           offsetof(ControlState, zoom), offsetof(ControlState, modelId), sizeof(ControlInput),\n"
        "           offsetof(ControlInput, action), offsetof(ControlInput, value));\n"
        "    return 0;\n}\n"
    )
    binary = tmp_path / "probe"
    subprocess.run(["gcc", "-std=c11", f"-I{RENDERER_SRC}", str(probe), "-o", str(binary)], check=True)
    sizes = [int(size) for size in subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout.split()]
    assert sizes == [STATE_STRUCT.size, 4, 8, 12, INPUT_STRUCT.size, 1, 4]


def test_state_round_trip():
    state = CameraState(123.5, 20.0, 1.25, "cup")
    assert DecodeState(EncodeState(state)) == state


def test_an_empty_model_id_round_trips():
    assert DecodeState(EncodeState(CameraState(0.0, 20.0, 1.0, ""))).modelId == ""


def test_a_model_id_that_does_not_fit_is_rejected():
    with pytest.raises(ValueError):
        EncodeState(CameraState(0.0, 20.0, 1.0, "x" * MODEL_ID_BYTES))


def test_decoder_rebuilds_records_cut_anywhere():
    events = [
        RendererInput(InputKind.ACTION_PRESS, InputAction.YAW_LEFT, 0.0),
        RendererInput(InputKind.MOUSE_DELTA, 0, -12.5),
        RendererInput(InputKind.ACTION_RELEASE, InputAction.YAW_LEFT, 0.0),
    ]
    stream = b"".join(EncodeInput(event) for event in events)
    for cut in range(len(stream) + 1):
        decoder = InputDecoder()
        assert decoder.Feed(stream[:cut]) + decoder.Feed(stream[cut:]) == events


def test_decoder_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        InputDecoder().Feed(INPUT_STRUCT.pack(99, 0, 0, 0.0))


@needsGcc
def test_the_c_link_exchanges_records_with_python(tmp_path):
    harness = tmp_path / "harness.c"
    harness.write_text(
        '#include <stdio.h>\n#include <unistd.h>\n#include "control-link.h"\n'
        "int main(int argc, char **argv) {\n"
        "    ControlLink link; ControlLinkInit(&link);\n"
        "    if (argc < 2 || !ControlLinkTryConnect(&link, argv[1])) return 2;\n"
        "    ControlInput press = {.kind = CONTROL_KIND_ACTION_PRESS, .action = CONTROL_ACTION_ZOOM_IN};\n"
        "    ControlInput mouse = {.kind = CONTROL_KIND_MOUSE_DELTA, .value = -3.5f};\n"
        "    if (!ControlLinkSend(&link, &press) || !ControlLinkSend(&link, &mouse)) return 3;\n"
        "    ControlState state = {0};\n"
        "    for (int attempt = 0; attempt < 300 && state.yawDeg < 90.5f; attempt++) {\n"
        "        ControlLinkReceive(&link, &state);\n"
        "        usleep(10000);\n"
        "    }\n"
        '    printf("%.2f %.2f %.2f %s\\n", state.yawDeg, state.pitchDeg, state.zoom, state.modelId);\n'
        "    ControlLinkClose(&link);\n"
        "    return ControlLinkIsConnected(&link) ? 4 : 0;\n}\n"
    )
    binary = tmp_path / "harness"
    subprocess.run(
        ["gcc", "-std=gnu99", "-Wall", "-Wextra", "-Werror", f"-I{RENDERER_SRC}", str(harness), str(RENDERER_SRC / "control-link.c"), "-o", str(binary)],
        check=True,
        capture_output=True,
    )

    socketPath = tmp_path / "renderer.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(socketPath))
        server.listen(1)
        process = subprocess.Popen([str(binary), str(socketPath)], stdout=subprocess.PIPE, text=True)
        connection, _ = server.accept()
        with connection:
            decoder = InputDecoder()
            received: list[RendererInput] = []
            while len(received) < 2:
                received += decoder.Feed(connection.recv(64))
            assert received == [
                RendererInput(InputKind.ACTION_PRESS, InputAction.ZOOM_IN, 0.0),
                RendererInput(InputKind.MOUSE_DELTA, 0, -3.5),
            ]
            encoded = EncodeState(CameraState(90.0, 20.0, 1.5, "cup"))
            connection.sendall(encoded[:10])
            connection.sendall(encoded[10:] + EncodeState(CameraState(91.0, 20.0, 1.5, "cup")))
            output, _ = process.communicate(timeout=10)
    assert process.returncode == 0
    assert output.split() == ["91.00", "20.00", "1.50", "cup"]
