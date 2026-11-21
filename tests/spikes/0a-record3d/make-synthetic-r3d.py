#!/usr/bin/env python3
"""Write a synthetic `.r3d` (the ArUco plate seen from a full turn of known poses).

Usage:
    uv run tests/spikes/0a-record3d/make-synthetic-r3d.py synthetic.r3d

Lets the 0a/0b tools be dry-run without a phone:
    uv run tests/spikes/0a-record3d/inspect-r3d.py synthetic.r3d
    uv run tests/spikes/0b-ring-pose/track-ring.py --r3d synthetic.r3d
The expected azimuth of frame i is i * --step-deg.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from capture_service.synthetic_r3d import WriteSyntheticTurntableR3d
from capture_service.synthetic_ring import ViewPose


def Main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", type=Path)
    parser.add_argument("--step-deg", type=float, default=10.0)
    parser.add_argument("--elevation-deg", type=float, default=35.0)
    parser.add_argument("--distance-mm", type=float, default=350.0)
    args = parser.parse_args()
    count = int(round(360.0 / args.step_deg))
    poses = [ViewPose(i * args.step_deg, args.elevation_deg, args.distance_mm) for i in range(count)]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    WriteSyntheticTurntableR3d(args.out, poses)
    print(f"wrote {count} frames to {args.out}")


if __name__ == "__main__":
    Main()
