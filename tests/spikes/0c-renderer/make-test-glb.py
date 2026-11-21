#!/usr/bin/env python3
"""Generate a vertex-coloured test GLB with a fixed triangle budget (spike 0c).

Usage:
    uv run tests/spikes/0c-renderer/make-test-glb.py renderer/models/lod-low.glb --triangles 8000
    uv run tests/spikes/0c-renderer/make-test-glb.py renderer/models/lod-high.glb --triangles 30000

The shape is a lumpy sphere (about the size of a scanned object, in metres) so
the lighting and the silhouette look like a real scan rather than a perfect ball.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np

from astrea_common.glb import WriteGlb

OBJECT_RADIUS_M = 0.06


def LumpySphere(triangles: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """UV sphere with `2 * slices * (stacks - 1)` triangles, at least `triangles`."""
    side = max(4, math.ceil(math.sqrt(triangles / 2.0)))
    slices, stacks = side, side + 1
    theta = np.linspace(0.0, math.pi, stacks + 1)
    phi = np.linspace(0.0, 2.0 * math.pi, slices, endpoint=False)
    thetaGrid, phiGrid = np.meshgrid(theta[1:-1], phi, indexing="ij")
    directions = np.stack(
        [np.sin(thetaGrid) * np.cos(phiGrid), np.cos(thetaGrid), np.sin(thetaGrid) * np.sin(phiGrid)], axis=-1
    ).reshape(-1, 3)
    directions = np.concatenate([[[0.0, 1.0, 0.0]], directions, [[0.0, -1.0, 0.0]]])
    x, y, z = directions.T
    bumps = 1.0 + 0.12 * np.sin(5 * x + 1.3) * np.sin(4 * z) + 0.08 * np.sin(7 * y + 0.5) + 0.05 * np.sin(11 * x * z)
    vertices = directions * (OBJECT_RADIUS_M * bumps)[:, None]

    ringCount = stacks - 1
    bottom = len(vertices) - 1

    def Ring(i: int, j: int) -> int:
        return 1 + i * slices + (j % slices)

    faces = []
    for j in range(slices):
        faces.append([0, Ring(0, j + 1), Ring(0, j)])
        faces.append([bottom, Ring(ringCount - 1, j), Ring(ringCount - 1, j + 1)])
    for i in range(ringCount - 1):
        for j in range(slices):
            a, b, c, d = Ring(i, j), Ring(i, j + 1), Ring(i + 1, j), Ring(i + 1, j + 1)
            faces.append([a, b, d])
            faces.append([a, d, c])
    faces = np.array(faces, dtype=np.int64)

    heights = (y + 1.0) / 2.0
    colors = np.stack([200 - 120 * heights, 120 + 80 * heights, 90 + 150 * heights], axis=1)
    colors = np.clip(colors * (0.85 + 0.15 * bumps[:, None]), 0, 255).astype(np.uint8)
    return vertices.astype(np.float32), faces, colors


def Main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", type=Path)
    parser.add_argument("--triangles", type=int, default=8000)
    args = parser.parse_args()
    vertices, faces, colors = LumpySphere(args.triangles)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    WriteGlb(args.out, vertices, faces, colors)
    print(f"wrote {args.out}: {len(faces)} triangles, {len(vertices)} vertices")


if __name__ == "__main__":
    Main()
