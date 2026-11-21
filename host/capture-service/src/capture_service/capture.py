"""Scan capture: tracks the ArUco ring, saves a keyframe every ~10° of turntable rotation and writes a session.

Usage:
    uv run astrea-capture --name cup                       # live Record3D stream over USB
    uv run astrea-capture --name cup --r3d capture.r3d     # replay a Record3D export
    uv run astrea-capture --name cup --layout hardware/aruco-ring/out/ring-layout.json --rotate 0

Turn the plate by hand and pause every few degrees; a keyframe is saved when the pose is stable
for --stable-seconds (1.2 s; every frame in that window is averaged) and the plate advanced at least --step-deg since the previous keyframe. The window shows the
ring pose, the saved keyframes and a coverage ring with one arc per 10° sector. Press q or close the
window to finish. Sessions go to out/scans/<name>/ and are reconstructed with `astrea-reconstruct`.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

import cv2

from astrea_common.aruco_ring import RingLayout
from astrea_common.rgbd_frame import RgbdFrame
from astrea_common.view_package import ListViewPaths, SessionManifest, WriteManifest
from capture_service.coverage import SECTOR_COUNT, DrawCoverageRing
from capture_service.frame_dump import LivePreview
from capture_service.keyframe_recorder import DEFAULT_AVERAGE_FRAMES, KeyframeRecorder
from capture_service.keyframe_selector import DEFAULT_MAX_ANGLE_DRIFT_DEG, DEFAULT_MAX_DISTANCE_DRIFT_MM, DEFAULT_STABLE_SECONDS, DEFAULT_STEP_DEG, SelectorParams
from capture_service.r3d_reader import R3dReader
from capture_service.record3d_stream import ListDevices, Record3dStream, Record3dUnavailable
from capture_service.ring_overlay import RingOverlay
from capture_service.stream_viewer import IDLE_POLL_MS, WINDOW_NAME, MeasuredFps, QuitRequested

DEFAULT_OUT_DIR = Path("out/scans")
DEFAULT_ROTATION_DEG = 90
COVERAGE_RING_RADIUS_PX = 40
COVERAGE_RING_MARGIN_PX = 60
SAVED_FLASH_FRAMES = 8
FLASH_COLOR = (0, 255, 0)


def ParseArgs() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="session name, saved under --out-dir/<name>")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--layout", type=Path, default=None, help="ring-layout.json from generate-ring.py")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--list", action="store_true", help="list connected devices and exit")
    parser.add_argument("--r3d", type=Path, default=None, help="replay a Record3D export instead of the USB stream")
    parser.add_argument("--flip-horizontal", action="store_true", help="mirror frames (use if the front camera image is mirrored)")
    parser.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=DEFAULT_ROTATION_DEG, help="rotate the preview counterclockwise")
    parser.add_argument("--step-deg", type=float, default=DEFAULT_STEP_DEG)
    parser.add_argument("--stable-seconds", type=float, default=DEFAULT_STABLE_SECONDS)
    parser.add_argument("--max-angle-drift-deg", type=float, default=DEFAULT_MAX_ANGLE_DRIFT_DEG)
    parser.add_argument("--max-distance-drift-mm", type=float, default=DEFAULT_MAX_DISTANCE_DRIFT_MM)
    parser.add_argument("--average-frames", type=int, default=DEFAULT_AVERAGE_FRAMES, help="most frames of the stable window to average")
    return parser.parse_args()


def StreamFrames(stream: Record3dStream) -> Iterator[RgbdFrame | None]:
    """Yield live frames; None means no frame arrived in time, so the caller can poll the window."""
    while not stream.stopped:
        try:
            yield stream.Read()
        except TimeoutError:
            yield None


def DrawCaptureStatus(panel, recorder: KeyframeRecorder, currentAzimuthDeg: float | None, justSaved: bool) -> None:
    center = (panel.shape[1] - COVERAGE_RING_MARGIN_PX, panel.shape[0] - COVERAGE_RING_MARGIN_PX)
    DrawCoverageRing(panel, recorder.coverage, currentAzimuthDeg, center, COVERAGE_RING_RADIUS_PX)
    text = f"keyframes {recorder.viewCount}  sectors {recorder.coverage.coveredCount}/{SECTOR_COUNT}"
    cv2.putText(panel, text, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, FLASH_COLOR if justSaved else (255, 255, 255), 2)


def Run(frames: Iterator[RgbdFrame | None], recorder: KeyframeRecorder, overlay: RingOverlay, args: argparse.Namespace) -> None:
    arrivals: deque = deque(maxlen=30)
    flashFrames = 0
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
    for frame in frames:
        if frame is None:
            if QuitRequested(IDLE_POLL_MS):
                break
            continue
        if args.flip_horizontal:
            frame = frame.MirroredHorizontally()
        arrivals.append(time.monotonic())
        pose, annotation = overlay.Track(frame)
        view = recorder.Feed(frame, pose)
        if view is not None:
            flashFrames = SAVED_FLASH_FRAMES
            print(f"keyframe {view.index:3d}  az {view.azimuthDeg:6.2f}  el {view.elevationDeg:5.1f}  reproj {view.reprojectionErrorPx:.2f} px")
        panel = LivePreview(frame, MeasuredFps(arrivals), annotation, args.rotate)
        DrawCaptureStatus(panel, recorder, None if pose is None else pose.azimuthDeg, flashFrames > 0)
        flashFrames = max(0, flashFrames - 1)
        cv2.imshow(WINDOW_NAME, panel)
        if QuitRequested(1):
            break


def WriteSessionManifest(sessionDir: Path, name: str, layout: RingLayout, deviceType: str, args: argparse.Namespace, selectorParams: SelectorParams, recorder: KeyframeRecorder) -> None:
    parameters = {"selector": asdict(selectorParams), "averageFrames": args.average_frames}
    manifest = SessionManifest(name, asdict(layout), asdict(recorder.lastView.intrinsics), deviceType, args.flip_horizontal, parameters, recorder.viewCount)
    WriteManifest(sessionDir, manifest)


def Main() -> None:
    args = ParseArgs()
    if args.list:
        for index, (productId, udid) in enumerate(ListDevices()):
            print(f"[{index}] product id {productId}, udid {udid}")
        return
    sessionDir = args.out_dir / args.name
    if ListViewPaths(sessionDir):
        sys.exit(f"{sessionDir} already has views; choose another --name")
    layout = RingLayout.Load(args.layout)
    selectorParams = SelectorParams(args.step_deg, args.stable_seconds, args.max_angle_drift_deg, args.max_distance_drift_mm)
    recorder = KeyframeRecorder(sessionDir, selectorParams, args.average_frames)
    overlay = RingOverlay(layout)
    deviceType = "unknown"
    try:
        if args.r3d is not None:
            with R3dReader(args.r3d) as reader:
                Run(iter(reader), recorder, overlay, args)
            deviceType = "r3d"
        else:
            with Record3dStream(args.device) as stream:
                deviceType = "TrueDepth" if stream.isTrueDepth else "LiDAR"
                print(f"connected, device type: {deviceType}")
                Run(StreamFrames(stream), recorder, overlay, args)
    except Record3dUnavailable as error:
        sys.exit(str(error))
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
    if recorder.lastView is None:
        sys.exit("no keyframes were saved; check that the ring is visible and the plate advances by at least --step-deg")
    WriteSessionManifest(sessionDir, args.name, layout, deviceType, args, selectorParams, recorder)
    print(f"saved {recorder.viewCount} keyframes, {recorder.coverage.coveredCount}/{SECTOR_COUNT} sectors, in {sessionDir}")


if __name__ == "__main__":
    Main()
