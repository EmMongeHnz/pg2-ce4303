"""Human-readable summaries and on-disk dumps of RGB-D frames, used by the 0a/0b spikes."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from astrea_common.point_cloud import BackProject, WritePly
from astrea_common.rgbd_frame import RgbdFrame

NEAR_M = 0.15
FAR_M = 0.8


def DescribeFrame(frame: RgbdFrame) -> str:
    depth = frame.depthM
    valid = depth[depth > 0]
    rgbK = frame.rgbIntrinsics
    depthK = frame.depthIntrinsics
    lines = [
        f"frame {frame.index}: rgb {frame.rgb.shape[1]}x{frame.rgb.shape[0]}, depth {depth.shape[1]}x{depth.shape[0]}",
        f"  rgb intrinsics   fx={rgbK.fx:.2f} fy={rgbK.fy:.2f} cx={rgbK.cx:.2f} cy={rgbK.cy:.2f}",
        f"  depth intrinsics fx={depthK.fx:.2f} fy={depthK.fy:.2f} cx={depthK.cx:.2f} cy={depthK.cy:.2f}",
    ]
    if len(valid):
        lines.append(
            f"  depth valid {100 * len(valid) / depth.size:.1f} %, "
            f"min {valid.min():.3f} m, median {np.median(valid):.3f} m, max {valid.max():.3f} m"
        )
    else:
        lines.append("  depth has no valid pixels")
    return "\n".join(lines)


def DepthPreview(depthM: np.ndarray) -> np.ndarray:
    normalized = np.clip((depthM - NEAR_M) / (FAR_M - NEAR_M), 0.0, 1.0)
    preview = cv2.applyColorMap((255 * (1.0 - normalized)).astype(np.uint8), cv2.COLORMAP_TURBO)
    preview[depthM <= 0] = 0
    return preview


def SaveFrame(frame: RgbdFrame, directory: Path) -> None:
    """Write rgb.png, depth.png (colour preview), depth.npy and a coloured PLY for one frame."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"frame-{frame.index:05d}"
    cv2.imwrite(str(directory / f"{stem}-rgb.png"), frame.Bgr())
    cv2.imwrite(str(directory / f"{stem}-depth.png"), DepthPreview(frame.depthM))
    np.save(directory / f"{stem}-depth.npy", frame.depthM)
    points, validIndices = BackProject(frame.depthM, frame.depthIntrinsics)
    colors = frame.RgbAtDepthResolution().reshape(-1, 3)[validIndices]
    WritePly(directory / f"{stem}.ply", points, colors)
