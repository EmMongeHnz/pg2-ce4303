"""Per-view depth clean-up before TSDF integration.

Every step looks at one view only and uses no randomness, so a view gives the same cleaned depth on any
node and the exact merge of partial volumes still equals single-node fusion. The plate is removed with
the plane given by the ArUco pose (z = 0 in the plate frame) instead of fitting one per view.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from astrea_common.point_cloud import BackProject
from astrea_common.view_package import ViewPackage

MM_PER_METRE = 1000.0
NEIGHBOUR_OFFSETS = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0)]


@dataclass(frozen=True)
class PreprocessParams:
    minDepthMm: float = 200.0
    maxDepthMm: float = 500.0
    plateMarginMm: float = 4.0
    maxHeightMm: float = 230.0
    cylinderRadiusMm: float = 100.0
    supportDepthMm: float = 8.0
    minSupportPixels: int = 3
    minComponentPixels: int = 30


def PreprocessView(view: ViewPackage, params: PreprocessParams = PreprocessParams()) -> np.ndarray:
    """Return the cleaned depth in millimetres, float32, with 0 where a pixel was removed."""
    depthMm = np.asarray(view.depthM, dtype=np.float32) * MM_PER_METRE
    depthMm[(depthMm < params.minDepthMm) | (depthMm > params.maxDepthMm)] = 0.0
    depthMm = CropToObjectVolume(depthMm, view, params)
    depthMm = RemoveUnsupportedPixels(depthMm, params.supportDepthMm, params.minSupportPixels)
    return RemoveSmallComponents(depthMm, params.minComponentPixels)


def CropToObjectVolume(depthMm: np.ndarray, view: ViewPackage, params: PreprocessParams) -> np.ndarray:
    """Keep only pixels whose 3D point lies above the plate and inside the scan cylinder."""
    pointsCamera, flatIndices = BackProject(depthMm, view.intrinsics)
    pointsPlate = (pointsCamera - view.translationMm) @ view.rotation
    radius = np.hypot(pointsPlate[:, 0], pointsPlate[:, 1])
    inside = (pointsPlate[:, 2] >= params.plateMarginMm) & (pointsPlate[:, 2] <= params.maxHeightMm) & (radius <= params.cylinderRadiusMm)
    cropped = depthMm.copy().reshape(-1)
    cropped[flatIndices[~inside]] = 0.0
    return cropped.reshape(depthMm.shape)


def RemoveUnsupportedPixels(depthMm: np.ndarray, supportDepthMm: float, minSupportPixels: int) -> np.ndarray:
    """Drop flying pixels: those with fewer than `minSupportPixels` neighbours at a similar depth."""
    padded = np.pad(depthMm, 1)
    height, width = depthMm.shape
    support = np.zeros(depthMm.shape, dtype=np.int32)
    for dy, dx in NEIGHBOUR_OFFSETS:
        neighbour = padded[1 + dy : 1 + dy + height, 1 + dx : 1 + dx + width]
        support += (neighbour > 0) & (np.abs(neighbour - depthMm) <= supportDepthMm)
    return np.where(support >= minSupportPixels, depthMm, 0.0).astype(np.float32)


def RemoveSmallComponents(depthMm: np.ndarray, minPixels: int) -> np.ndarray:
    """Drop connected groups of valid pixels smaller than `minPixels`."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats((depthMm > 0).astype(np.uint8), connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= minPixels
    keep[0] = False
    return np.where(keep[labels], depthMm, 0.0).astype(np.float32)
