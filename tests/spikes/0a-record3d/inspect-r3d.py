#!/usr/bin/env python3
"""Spike 0a, fallback path: inspect a Record3D `.r3d` export.

Usage:
    uv run tests/spikes/0a-record3d/inspect-r3d.py capture.r3d
    uv run tests/spikes/0a-record3d/inspect-r3d.py capture.r3d --frames 0 30 60 --save-dir out/

Prints the metadata and per-frame intrinsics/depth statistics. With --save-dir it
writes RGB, depth preview, depth .npy and a coloured point cloud (.ply) per frame:
open the .ply in MeshLab or CloudCompare to check that flat surfaces look flat
and that sizes are right (intrinsics and depth units).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from capture_service.frame_dump import DescribeFrame, SaveFrame
from capture_service.r3d_reader import R3dReader

METADATA_KEYS_TO_HIDE = ("poses", "perFrameIntrinsicCoeffs")


def ParseArgs() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("r3d", type=Path)
    parser.add_argument("--frames", type=int, nargs="*", default=None, help="frame indices (default: first, middle, last)")
    parser.add_argument("--save-dir", type=Path, default=None)
    return parser.parse_args()


def Main() -> None:
    args = ParseArgs()
    with R3dReader(args.r3d) as reader:
        shown = {key: value for key, value in reader.metadata.items() if key not in METADATA_KEYS_TO_HIDE}
        print(json.dumps(shown, indent=2))
        poses = reader.metadata.get("poses") or []
        print(f"frames: {len(reader)}, poses: {len(poses)}, fps: {reader.fps}")
        if not len(reader):
            raise SystemExit("the archive has no depth frames")
        indices = args.frames
        if indices is None:
            frameIndices = reader.frameIndices
            indices = sorted({frameIndices[0], frameIndices[len(frameIndices) // 2], frameIndices[-1]})
        for index in indices:
            frame = reader.ReadFrame(index)
            print(DescribeFrame(frame))
            if args.save_dir:
                SaveFrame(frame, args.save_dir)
        if args.save_dir:
            print(f"saved frames to {args.save_dir}")


if __name__ == "__main__":
    Main()
