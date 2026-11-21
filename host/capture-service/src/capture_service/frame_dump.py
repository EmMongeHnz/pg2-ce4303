"""Human-readable summaries and on-disk dumps of RGB-D frames, used by the 0a/0b spikes."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from astrea_common.point_cloud import BackProject, WritePly
from astrea_common.rgbd_frame import RgbdFrame

NEAR_M = 0.15
FAR_M = 0.8
STATS_STRIDE = 4


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
    nearest = np.minimum(depthM, FAR_M)
    scaled = cv2.convertScaleAbs(nearest, alpha=-255.0 / (FAR_M - NEAR_M), beta=255.0 * FAR_M / (FAR_M - NEAR_M))
    preview = cv2.applyColorMap(scaled, cv2.COLORMAP_TURBO)
    hasDepth = cv2.compare(depthM, 0.0, cv2.CMP_GT)
    return cv2.bitwise_and(preview, preview, mask=hasDepth)


def LivePreview(frame: RgbdFrame, fps: float) -> np.ndarray:
    """RGB and coloured depth side by side, with the frame rate and depth statistics on top."""
    rgb = cv2.cvtColor(frame.RgbAtDepthResolution(), cv2.COLOR_RGB2BGR)
    panel = np.hstack([rgb, DepthPreview(frame.depthM)])
    sampled = frame.depthM[::STATS_STRIDE, ::STATS_STRIDE]
    valid = sampled[sampled > 0]
    median = f"{np.median(valid):.3f} m" if len(valid) else "n/a"
    text = f"{fps:.1f} fps  frame {frame.index}  valid {100 * len(valid) / sampled.size:.0f} %  median {median}"
    cv2.putText(panel, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    scale = f"depth {NEAR_M:.2f} m (red) to {FAR_M:.2f} m (blue), black = no data"
    cv2.putText(panel, scale, (rgb.shape[1] + 10, panel.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return panel


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
