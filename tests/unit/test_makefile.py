"""The root Makefile: each target expands (make --dry-run) to the commands of its step, with the options of the full run."""

import shutil
import subprocess
from pathlib import Path

import pytest

from coordinator.host_service import FindModelIds

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def DryRun(*arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["make", "-n", "-C", str(REPO), "RENDERER_BIN=/bin/true", *arguments], capture_output=True, text=True)
    if check:
        assert result.returncode == 0, result.stderr
    return result


def test_help_lists_one_target_per_step_of_the_run():
    output = subprocess.run(["make", "-C", str(REPO), "help"], capture_output=True, text=True, check=True).stdout
    for target in ["capture", "worker", "reconstruct", "models", "host-service", "viewer", "agent", "hologram", "monitor", "test-san"]:
        assert f"  {target} " in output


def test_capture_needs_a_name_and_starts_the_capture():
    assert "SCAN is required" in DryRun("capture", check=False).stderr
    assert "uv run astrea-capture --name figura" in DryRun("capture", "SCAN=figura").stdout
    assert "--flip-horizontal" in DryRun("capture", "SCAN=figura", "CAPTURE_ARGS=--flip-horizontal").stdout


def test_worker_needs_a_name_and_the_pi_offers_chacha_first():
    assert "NAME is required" in DryRun("worker", check=False).stderr
    host = DryRun("worker", "NAME=host").stdout
    assert "--name host" in host and "--prefer-chacha20" not in host and "--verbose" in host
    assert "--name pi" in DryRun("worker", "NAME=pi").stdout and "--prefer-chacha20" in DryRun("worker", "NAME=pi").stdout
    assert "--prefer-chacha20" in DryRun("worker", "NAME=worker-b", "CHACHA=1").stdout
    assert "--max-grid-mib 512" in DryRun("worker", "NAME=pi", "MAX_GRID_MIB=512").stdout


def test_reconstruct_uses_the_options_of_the_full_run_and_only_adds_the_others_when_given():
    default = DryRun("reconstruct", "SCAN=kana_2").stdout
    assert "astrea-coordinator --config config.toml --verbose reconstruct out/scans/kana_2" in default
    assert "--min-workers 2" in default and "--refine-poses azimuth" in default
    assert "--voxel-mm" not in default and "--job-timeout" not in default and "--out" not in default
    custom = DryRun("reconstruct", "SCAN=kana_2", "WORKERS=3", "VOXEL_MM=1.3", "REFINE=none", "JOB_TIMEOUT=300", "OUT=out/x").stdout
    assert "--min-workers 3" in custom and "--voxel-mm 1.3" in custom and "--refine-poses none" in custom
    assert "--job-timeout 300" in custom and "--out out/x" in custom
    assert "there is no scan out/scans/kana_2" in default, "the recipe must check that the scan exists before starting the hub"


def test_models_copies_the_low_detail_model_with_the_name_of_the_scan():
    output = DryRun("models", "SCAN=figura").stdout
    assert "cp out/scans/figura/model/lod-low.glb out/models/figura.glb" in output


def test_the_host_service_copies_the_model_first_only_when_a_scan_is_given():
    with_scan = DryRun("host-service", "SCAN=figura").stdout
    assert with_scan.index("cp out/scans/figura") < with_scan.index("astrea-host")
    without = DryRun("host-service").stdout
    assert "cp " not in without and "astrea-host --config config.toml --models-dir out/models --renderer-socket /tmp/astrea-viewer.sock" in without


def test_both_viewers_start_on_the_first_model_like_the_host_service(tmp_path):
    for name in ["b.glb", "a.glb", "a-b.glb", "notes.txt"]:
        (tmp_path / name).write_bytes(b"")
    first = f"{tmp_path}/{FindModelIds(tmp_path)[0]}.glb"
    viewer = DryRun("viewer", f"MODELS_DIR={tmp_path}").stdout
    hologram = DryRun("hologram", f"MODELS_DIR={tmp_path}").stdout
    assert f"--mode viewer --up-axis z --model {first} --control-socket /tmp/astrea-viewer.sock --models-dir {tmp_path}" in viewer
    assert f"--mode hologram --up-axis z --model {first} --control-socket /tmp/astrea-hologram.sock --models-dir {tmp_path}" in hologram
    assert "--model /models/x.glb" in DryRun("hologram", "MODEL=/models/x.glb").stdout


def test_the_viewers_say_what_is_missing_when_there_are_no_models(tmp_path):
    output = DryRun("hologram", f"MODELS_DIR={tmp_path}").stdout
    assert "there are no models" in output


def test_the_agent_uses_the_hologram_socket_and_the_models_folder():
    output = DryRun("agent").stdout
    assert "uv run astrea-device-agent --config config.toml --name pi --models-dir out/models --renderer-socket /tmp/astrea-hologram.sock --verbose" in output


def test_the_variables_can_be_overridden():
    output = DryRun("worker", "NAME=host", "CONFIG=other.toml").stdout
    assert "--config other.toml" in output
    assert "-DRENDERER_PLATFORM=DRM" in subprocess.run(["make", "-n", "-C", str(REPO), "renderer", "RENDERER_PLATFORM=DRM"], capture_output=True, text=True).stdout
