"""Human-readable summaries and on-disk dumps of RGB-D frames, used by the 0a/0b spikes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from astrea_common.point_cloud import BackProject, WritePly
from astrea_common.rgbd_frame import RgbdFrame

NEAR_M = 0.15
FAR_M = 0.8
STATS_STRIDE = 4
STATUS_TOP_PX = 38
STATUS_LEFT_PX = 6
STATUS_LINE_HEIGHT_PX = 22
STATUS_PADDING_PX = 6
STATUS_TEXT_SCALE = 0.55
STATUS_BOX_OPACITY = 0.6
COUNTERCLOCKWISE_FLAGS = {
    0: None,
    90: cv2.ROTATE_90_COUNTERCLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_CLOCKWISE,
}


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


@dataclass(frozen=True)
class RgbAnnotation:
    """Annotated copy of the RGB frame (same orientation as the frame) plus a status block to draw upright."""

    image: np.ndarray
    statusLines: list[str]
    statusColor: tuple[int, int, int]


def RotateCounterclockwise(image: np.ndarray, degrees: int) -> np.ndarray:
    flag = COUNTERCLOCKWISE_FLAGS[degrees]
    return image if flag is None else cv2.rotate(image, flag)


def DrawStatusBlock(image: np.ndarray, lines: list[str], color: tuple[int, int, int]) -> None:
    """Draw text lines over a darkened box so they stay readable on any background."""
    textWidth = max(cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, STATUS_TEXT_SCALE, 1)[0][0] for line in lines)
    top, left = STATUS_TOP_PX, STATUS_LEFT_PX
    bottom = top + len(lines) * STATUS_LINE_HEIGHT_PX + STATUS_PADDING_PX
    region = image[top:bottom, left : left + textWidth + 2 * STATUS_PADDING_PX]
    cv2.addWeighted(region, 1.0 - STATUS_BOX_OPACITY, np.zeros_like(region), STATUS_BOX_OPACITY, 0.0, dst=region)
    for row, line in enumerate(lines):
        baseline = top + STATUS_PADDING_PX + (row + 1) * STATUS_LINE_HEIGHT_PX - 6
        cv2.putText(image, line, (left + STATUS_PADDING_PX, baseline), cv2.FONT_HERSHEY_SIMPLEX, STATUS_TEXT_SCALE, color, 1, cv2.LINE_AA)


def LivePreview(frame: RgbdFrame, fps: float, annotation: RgbAnnotation | None = None, rotationDeg: int = 0) -> np.ndarray:
    """RGB and coloured depth side by side, with the frame rate and depth statistics on top.

    `rotationDeg` turns both panels counterclockwise (0, 90, 180 or 270) before any text is drawn.
    """
    height, width = frame.depthM.shape
    if annotation is None:
        rgb = cv2.cvtColor(frame.RgbAtDepthResolution(), cv2.COLOR_RGB2BGR)
    else:
        rgb = cv2.resize(annotation.image, (width, height), interpolation=cv2.INTER_AREA)
    rgb = np.ascontiguousarray(RotateCounterclockwise(rgb, rotationDeg))
    depth = np.ascontiguousarray(RotateCounterclockwise(DepthPreview(frame.depthM), rotationDeg))
    if annotation is not None:
        DrawStatusBlock(rgb, annotation.statusLines, annotation.statusColor)
    panel = np.hstack([rgb, depth])
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
