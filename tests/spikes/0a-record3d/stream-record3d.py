#!/usr/bin/env python3
"""Spike 0a, primary path: receive RGB-D frames from Record3D over USB.

Usage:
    uv sync --extra record3d
    uv run tests/spikes/0a-record3d/stream-record3d.py --list
    uv run tests/spikes/0a-record3d/stream-record3d.py --seconds 10 --save-dir out/ --save-every 60
    uv run tests/spikes/0a-record3d/stream-record3d.py --seconds 300 --show

Prints the measured frame rate, the frame sizes, intrinsics and depth statistics,
and optionally saves frames (RGB, depth, .ply) for an offline check.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

from capture_service.frame_dump import DescribeFrame, LivePreview, SaveFrame
from capture_service.record3d_stream import ListDevices, Record3dStream

REPORT_INTERVAL_S = 1.0


def ParseArgs() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="list connected devices and exit")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--save-dir", type=Path, default=None)
    parser.add_argument("--save-every", type=int, default=60, help="save one frame every N frames")
    parser.add_argument("--show", action="store_true", help="live window with RGB and depth side by side (q quits)")
    return parser.parse_args()


def Main() -> None:
    args = ParseArgs()
    if args.list:
        for index, (productId, udid) in enumerate(ListDevices()):
            print(f"[{index}] product id {productId}, udid {udid}")
        return
    stream = Record3dStream(args.device)
    print(f"connected, device type: {'TrueDepth' if stream.isTrueDepth else 'LiDAR'}")
    start = time.monotonic()
    lastReport = start
    framesSinceReport = 0
    totalFrames = 0
    currentFps = 0.0
    while time.monotonic() - start < args.seconds and not stream.stopped:
        frame = stream.Read()
        if totalFrames == 0:
            print(DescribeFrame(frame))
        if args.save_dir and totalFrames % args.save_every == 0:
            SaveFrame(frame, args.save_dir)
        totalFrames += 1
        framesSinceReport += 1
        now = time.monotonic()
        if now - lastReport >= REPORT_INTERVAL_S:
            currentFps = framesSinceReport / (now - lastReport)
            print(f"{currentFps:.1f} fps")
            lastReport, framesSinceReport = now, 0
        if args.show:
            cv2.imshow("stream-record3d", LivePreview(frame, currentFps))
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    elapsed = time.monotonic() - start
    print(f"received {totalFrames} frames in {elapsed:.1f} s ({totalFrames / elapsed:.1f} fps average)")


if __name__ == "__main__":
    Main()
