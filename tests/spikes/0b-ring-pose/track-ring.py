#!/usr/bin/env python3
"""Spike 0b, real part: track the printed ArUco ring in Record3D RGB frames.

Usage:
    uv run tests/spikes/0b-ring-pose/track-ring.py --r3d capture.r3d --csv poses.csv
    uv run tests/spikes/0b-ring-pose/track-ring.py --usb --seconds 30 --show
    uv run tests/spikes/0b-ring-pose/track-ring.py --r3d capture.r3d --flip-horizontal

For each frame it prints the turntable azimuth, the markers used and the
reprojection error, plus the depth-vs-pose residual at the marker centres, which
checks intrinsics, depth units and RGB/depth alignment (spike 0a) at the same time.
Use --flip-horizontal if no marker is ever detected (mirrored front-camera image).
"""

from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path

import cv2
import numpy as np

from astrea_common.aruco_ring import RingLayout
from capture_service.depth_check import MarkerDepthResidualsMm
from capture_service.ring_pose import RingPoseEstimator


def ParseArgs() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--r3d", type=Path)
    source.add_argument("--usb", action="store_true")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=30.0, help="duration for --usb")
    parser.add_argument("--layout", type=Path, default=None, help="ring-layout.json from generate-ring.py")
    parser.add_argument("--flip-horizontal", action="store_true")
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--show", action="store_true", help="show an annotated live window")
    return parser.parse_args()


def Frames(args):
    if args.r3d:
        from capture_service.r3d_reader import R3dReader

        with R3dReader(args.r3d) as reader:
            yield from reader
        return
    from capture_service.record3d_stream import Record3dStream

    with Record3dStream(args.device) as stream:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline and not stream.stopped:
            yield stream.Read()


def Annotate(bgr, estimator, detections, pose) -> np.ndarray:
    for corners in detections.values():
        cv2.polylines(bgr, [np.round(corners).astype(np.int32)], True, (0, 220, 0), 2)
    if pose is not None:
        rvec, _ = cv2.Rodrigues(pose.rotation)
        cv2.drawFrameAxes(bgr, estimator.cameraMatrix, estimator.distortion, rvec, pose.translationMm, 40)
        text = f"az {pose.azimuthDeg:6.2f}  el {pose.elevationDeg:5.1f}  markers {len(pose.markerIds)}"
        cv2.putText(bgr, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return bgr


def Main() -> None:
    args = ParseArgs()
    layout = RingLayout.Load(args.layout)
    estimator = None
    rows = []
    for frame in Frames(args):
        if args.flip_horizontal:
            frame = frame.MirroredHorizontally()
        if estimator is None:
            estimator = RingPoseEstimator(layout, frame.rgbIntrinsics.Matrix())
        bgr = frame.Bgr()
        detections = estimator.DetectMarkers(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        pose = estimator.EstimateFromDetections(detections)
        row = {"frame": frame.index, "markers": len(detections), "azimuthDeg": math.nan, "elevationDeg": math.nan,
               "distanceMm": math.nan, "reprojectionErrorPx": math.nan, "depthResidualMm": math.nan}
        if pose is not None:
            residuals = MarkerDepthResidualsMm(frame, pose, layout)
            row.update(azimuthDeg=pose.azimuthDeg, elevationDeg=pose.elevationDeg, distanceMm=pose.distanceMm,
                       reprojectionErrorPx=pose.reprojectionErrorPx,
                       depthResidualMm=float(np.median(residuals)) if residuals else math.nan)
            print(f"frame {frame.index:5d}  az {pose.azimuthDeg:7.2f}°  el {pose.elevationDeg:5.1f}°  "
                  f"dist {pose.distanceMm:6.1f} mm  markers {len(pose.markerIds):2d}  "
                  f"reproj {pose.reprojectionErrorPx:.2f} px  depth-vs-pose {row['depthResidualMm']:+.1f} mm")
        else:
            print(f"frame {frame.index:5d}  no pose ({len(detections)} markers)")
        rows.append(row)
        if args.show:
            cv2.imshow("track-ring", Annotate(bgr, estimator, detections, pose))
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    if args.csv and rows:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.csv, "w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    PrintSummary(rows)


def PrintSummary(rows: list[dict]) -> None:
    withPose = [row for row in rows if not math.isnan(row["azimuthDeg"])]
    print(f"\n{len(withPose)}/{len(rows)} frames with pose")
    if not withPose:
        print("no pose at all: check the layout, the lighting, or try --flip-horizontal")
        return
    azimuths = np.radians([row["azimuthDeg"] for row in withPose])
    meanVector = np.mean(np.exp(1j * azimuths))
    circularStdDeg = math.degrees(math.sqrt(-2.0 * math.log(max(abs(meanVector), 1e-12))))
    residuals = [row["depthResidualMm"] for row in withPose if not math.isnan(row["depthResidualMm"])]
    print(f"azimuth circular std: {circularStdDeg:.3f}° (only meaningful for a static recording)")
    if residuals:
        print(f"depth-vs-pose residual: median {np.median(residuals):+.1f} mm, "
              f"p90 |r| {np.percentile(np.abs(residuals), 90):.1f} mm")


if __name__ == "__main__":
    Main()
