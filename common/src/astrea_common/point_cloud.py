"""Back-projection of depth maps and ASCII PLY export, used to eyeball intrinsics and depth units."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from astrea_common.rgbd_frame import Intrinsics


def BackProject(depthM: np.ndarray, intrinsics: Intrinsics) -> tuple[np.ndarray, np.ndarray]:
    """Return (Nx3 points in camera frame, metres; flat indices of the valid pixels)."""
    height, width = depthM.shape
    v, u = np.indices((height, width))
    valid = depthM > 0
    z = depthM[valid]
    x = (u[valid] - intrinsics.cx) * z / intrinsics.fx
    y = (v[valid] - intrinsics.cy) * z / intrinsics.fy
    return np.stack([x, y, z], axis=1), np.flatnonzero(valid)


def WritePly(path: str | Path, points: np.ndarray, colors: np.ndarray | None = None) -> None:
    header = [
        "ply",
        "format ascii 1.0",
        f"element vertex {len(points)}",
        "property float x",
        "property float y",
        "property float z",
    ]
    if colors is not None:
        header += ["property uchar red", "property uchar green", "property uchar blue"]
    header.append("end_header")
    with open(path, "w") as file:
        file.write("\n".join(header) + "\n")
        for i, point in enumerate(points):
            line = f"{point[0]:.5f} {point[1]:.5f} {point[2]:.5f}"
            if colors is not None:
                r, g, b = colors[i]
                line += f" {int(r)} {int(g)} {int(b)}"
            file.write(line + "\n")
