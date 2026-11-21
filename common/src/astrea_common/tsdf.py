"""Truncated signed distance volume on a fixed common grid (NumPy only, shared by workers and coordinator).

Arrays are indexed [ix, iy, iz]; voxel (i, j, k) is centred at `originMm + voxelMm * (i, j, k)` in the
plate frame (millimetres, z up, the same frame as the ArUco ring pose). Depth maps are in millimetres.

Every observation adds a unit weight to a running weighted average and the weight is never capped,
so volumes built from disjoint sets of views merge exactly with `MergeVolumes`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from astrea_common.rgbd_frame import Intrinsics

TRUNCATION_VOXELS = 4.0
SLAB_PLANES = 8
MIN_CAMERA_Z_MM = 1.0


@dataclass(frozen=True)
class GridSpec:
    originMm: tuple[float, float, float]
    voxelMm: float
    shape: tuple[int, int, int]

    @staticmethod
    def Default() -> "GridSpec":
        """25 cm cube at 192 voxels per side, centred on the plate axis and starting just below the plate."""
        voxelMm = 250.0 / 192
        return GridSpec((-125.0 + voxelMm / 2, -125.0 + voxelMm / 2, -10.0 + voxelMm / 2), voxelMm, (192, 192, 192))

    def AxisMm(self, axis: int) -> np.ndarray:
        return self.originMm[axis] + self.voxelMm * np.arange(self.shape[axis])


class TsdfVolume:
    def __init__(self, grid: GridSpec, truncationVoxels: float = TRUNCATION_VOXELS) -> None:
        self.grid = grid
        self.truncationMm = truncationVoxels * grid.voxelMm
        self.tsdf = np.ones(grid.shape, dtype=np.float32)
        self.weight = np.zeros(grid.shape, dtype=np.float32)
        self.color = np.zeros((*grid.shape, 3), dtype=np.uint8)

    def Integrate(
        self,
        depthMm: np.ndarray,
        intrinsics: Intrinsics,
        rotation: np.ndarray,
        translationMm: np.ndarray,
        rgb: np.ndarray | None = None,
        minDepthMm: float = 0.0,
        maxDepthMm: float = math.inf,
    ) -> None:
        """Fuse one view. `xCamera = rotation @ xPlate + translationMm`; depth 0 means no measurement.

        Works on slabs of z planes so the temporaries stay small enough for a Pi 3.
        """
        xAxis, yAxis, zAxis = (self.grid.AxisMm(axis) for axis in range(3))
        for zStart in range(0, self.grid.shape[2], SLAB_PLANES):
            zStop = min(zStart + SLAB_PLANES, self.grid.shape[2])
            x, y, z = np.meshgrid(xAxis, yAxis, zAxis[zStart:zStop], indexing="ij")
            self._IntegrateSlab(slice(zStart, zStop), (x, y, z), depthMm, intrinsics, rotation, translationMm, rgb, minDepthMm, maxDepthMm)

    def _IntegrateSlab(self, planes, points, depthMm, intrinsics, rotation, translationMm, rgb, minDepthMm, maxDepthMm) -> None:
        x, y, z = points
        r = rotation
        xc = r[0, 0] * x + r[0, 1] * y + r[0, 2] * z + translationMm[0]
        yc = r[1, 0] * x + r[1, 1] * y + r[1, 2] * z + translationMm[1]
        zc = r[2, 0] * x + r[2, 1] * y + r[2, 2] * z + translationMm[2]
        inFront = zc > MIN_CAMERA_Z_MM
        safeZ = np.where(inFront, zc, 1.0)
        col = np.rint(intrinsics.fx * xc / safeZ + intrinsics.cx).astype(np.int64)
        row = np.rint(intrinsics.fy * yc / safeZ + intrinsics.cy).astype(np.int64)
        height, width = depthMm.shape
        inside = inFront & (col >= 0) & (col < width) & (row >= 0) & (row < height)
        measured = np.zeros_like(zc)
        measured[inside] = depthMm[row[inside], col[inside]]
        distance = measured - zc
        update = inside & (measured > 0) & (measured >= minDepthMm) & (measured <= maxDepthMm) & (distance >= -self.truncationMm)
        if not update.any():
            return
        observation = np.minimum(distance[update] / self.truncationMm, 1.0)
        weightView = self.weight[:, :, planes]
        tsdfView = self.tsdf[:, :, planes]
        oldWeight = weightView[update].astype(np.float64)
        newWeight = oldWeight + 1.0
        tsdfView[update] = (oldWeight * tsdfView[update] + observation) / newWeight
        if rgb is not None:
            colorView = self.color[:, :, planes]
            sampled = rgb[row[update], col[update]].astype(np.float64)
            blended = (oldWeight[:, None] * colorView[update] + sampled) / newWeight[:, None]
            colorView[update] = np.rint(blended).astype(np.uint8)
        weightView[update] = newWeight


def MergeVolumes(first: TsdfVolume, second: TsdfVolume) -> TsdfVolume:
    """Exact weighted merge: D = (w1 d1 + w2 d2) / (w1 + w2), the same for colour."""
    if first.grid != second.grid or first.truncationMm != second.truncationMm:
        raise ValueError("volumes must share the same grid and truncation")
    merged = TsdfVolume(first.grid, first.truncationMm / first.grid.voxelMm)
    total = first.weight.astype(np.float64) + second.weight
    observed = total > 0
    safeTotal = np.where(observed, total, 1.0)
    weightedTsdf = first.weight * first.tsdf.astype(np.float64) + second.weight * second.tsdf
    merged.tsdf = np.where(observed, weightedTsdf / safeTotal, 1.0).astype(np.float32)
    colorBlend = (first.weight[..., None] * first.color + second.weight[..., None] * second.color) / safeTotal[..., None]
    merged.color = np.rint(colorBlend).astype(np.uint8)
    merged.weight = total.astype(np.float32)
    return merged
