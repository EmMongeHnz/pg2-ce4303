"""Truncated signed distance volume on a fixed common grid (NumPy only, shared by workers and coordinator).

Arrays are indexed [ix, iy, iz]; voxel (i, j, k) is centred at `originMm + voxelMm * (i, j, k)` in the
plate frame (millimetres, z up, the same frame as the ArUco ring pose). Depth maps are in millimetres.

Every observation adds its weight (1 unless the view supplies a weight map) to a running weighted average
and the weight is never capped, so volumes built from disjoint sets of views merge exactly with
`MergeVolumes`. Colour is kept as a float32 weighted mean: a uint8 running mean stops changing once the
weight is large, because every new sample rounds to zero change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from astrea_common.rgbd_frame import Intrinsics

TRUNCATION_VOXELS = 4.0
SLAB_PLANES = 8
MIN_CAMERA_Z_MM = 1.0
PLATE_CLEARANCE_MM = 10.0
BYTES_PER_VOXEL = 4 + 4 + 12
DEFAULT_CUBE_MM = 250.0
DEFAULT_CUBE_VOXELS = 192
GRID_SIZE_EPSILON = 1e-9


@dataclass(frozen=True)
class GridSpec:
    originMm: tuple[float, float, float]
    voxelMm: float
    shape: tuple[int, int, int]

    @staticmethod
    def Default() -> "GridSpec":
        """25 cm cube at 192 voxels per side, centred on the plate axis and starting just below the plate."""
        return GridSpec.Cube(DEFAULT_CUBE_MM, DEFAULT_CUBE_MM / DEFAULT_CUBE_VOXELS)

    @staticmethod
    def Cube(sideMm: float, voxelMm: float) -> "GridSpec":
        count = math.ceil(sideMm / voxelMm - GRID_SIZE_EPSILON)
        half = voxelMm / 2
        return GridSpec((-sideMm / 2 + half, -sideMm / 2 + half, -PLATE_CLEARANCE_MM + half), voxelMm, (count, count, count))

    @staticmethod
    def FitToBounds(lowMm: np.ndarray, highMm: np.ndarray, voxelMm: float, marginMm: float) -> "GridSpec":
        """Smallest grid with the given voxel size that contains the box plus `marginMm` on every side."""
        low = np.asarray(lowMm, dtype=np.float64) - marginMm
        span = np.asarray(highMm, dtype=np.float64) + marginMm - low
        shape = tuple(int(math.ceil(side / voxelMm - GRID_SIZE_EPSILON)) + 1 for side in span)
        return GridSpec(tuple(float(value) for value in low), voxelMm, shape)

    @property
    def voxelCount(self) -> int:
        return int(np.prod(self.shape, dtype=np.int64))

    @property
    def memoryBytes(self) -> int:
        return self.voxelCount * BYTES_PER_VOXEL

    def AxisMm(self, axis: int) -> np.ndarray:
        return self.originMm[axis] + self.voxelMm * np.arange(self.shape[axis])


@dataclass(frozen=True)
class _View:
    depthMm: np.ndarray
    intrinsics: Intrinsics
    rotation: np.ndarray
    translationMm: np.ndarray
    rgb: np.ndarray | None
    minDepthMm: float
    maxDepthMm: float
    weights: np.ndarray | None
    bilinear: bool


class TsdfVolume:
    def __init__(self, grid: GridSpec, truncationVoxels: float = TRUNCATION_VOXELS, truncationMm: float | None = None) -> None:
        self.grid = grid
        self.truncationMm = truncationMm if truncationMm is not None else truncationVoxels * grid.voxelMm
        self.tsdf = np.ones(grid.shape, dtype=np.float32)
        self.weight = np.zeros(grid.shape, dtype=np.float32)
        self.color = np.zeros((*grid.shape, 3), dtype=np.float32)

    def Integrate(
        self,
        depthMm: np.ndarray,
        intrinsics: Intrinsics,
        rotation: np.ndarray,
        translationMm: np.ndarray,
        rgb: np.ndarray | None = None,
        minDepthMm: float = 0.0,
        maxDepthMm: float = math.inf,
        weights: np.ndarray | None = None,
        bilinear: bool = True,
    ) -> None:
        """Fuse one view. `xCamera = rotation @ xPlate + translationMm`; depth 0 means no measurement.

        `weights` is an optional per-pixel weight map (same shape as the depth). With `bilinear` the depth
        and colour are interpolated between the four surrounding pixels wherever those pixels lie on one
        surface; across depth jumps the nearest pixel is used, so silhouettes are not bridged.
        Works on slabs of z planes so the temporaries stay small enough for a Pi 3.
        """
        view = _View(depthMm, intrinsics, rotation, translationMm, rgb, minDepthMm, maxDepthMm, weights, bilinear)
        xAxis, yAxis, zAxis = (self.grid.AxisMm(axis) for axis in range(3))
        for zStart in range(0, self.grid.shape[2], SLAB_PLANES):
            zStop = min(zStart + SLAB_PLANES, self.grid.shape[2])
            x, y, z = np.meshgrid(xAxis, yAxis, zAxis[zStart:zStop], indexing="ij")
            self._IntegrateSlab(slice(zStart, zStop), (x, y, z), view)

    def _IntegrateSlab(self, planes, points, view: _View) -> None:
        x, y, z = points
        r = view.rotation
        t = view.translationMm
        xc = r[0, 0] * x + r[0, 1] * y + r[0, 2] * z + t[0]
        yc = r[1, 0] * x + r[1, 1] * y + r[1, 2] * z + t[1]
        zc = np.ascontiguousarray(r[2, 0] * x + r[2, 1] * y + r[2, 2] * z + t[2])
        inFront = zc > MIN_CAMERA_Z_MM
        safeZ = np.where(inFront, zc, 1.0)
        k = view.intrinsics
        u = k.fx * xc / safeZ + k.cx
        v = k.fy * yc / safeZ + k.cy
        col = np.rint(u).astype(np.int64)
        row = np.rint(v).astype(np.int64)
        height, width = view.depthMm.shape
        inside = inFront & (col >= 0) & (col < width) & (row >= 0) & (row < height)
        measured = np.zeros(zc.shape, dtype=zc.dtype)
        measured[inside] = view.depthMm[row[inside], col[inside]]
        coherent = self._RefineWithBilinearDepth(measured, inside, u, v, view.depthMm) if view.bilinear else np.zeros(zc.shape, dtype=bool)
        distance = measured - zc
        update = inside & (measured > 0) & (measured >= view.minDepthMm) & (measured <= view.maxDepthMm) & (distance >= -self.truncationMm)
        obsWeight = self._ObservationWeights(view, inside, row, col, update)
        if not update.any():
            return
        observation = np.minimum(distance[update] / self.truncationMm, 1.0)
        weightView = self.weight[:, :, planes]
        tsdfView = self.tsdf[:, :, planes]
        oldWeight = weightView[update].astype(np.float64)
        newWeight = oldWeight + obsWeight
        tsdfView[update] = (oldWeight * tsdfView[update] + obsWeight * observation) / newWeight
        if view.rgb is not None:
            colorView = self.color[:, :, planes]
            sampled = SampleRgb(view.rgb, u[update], v[update], coherent[update])
            blended = (oldWeight[:, None] * colorView[update] + obsWeight[:, None] * sampled) / newWeight[:, None]
            colorView[update] = blended.astype(np.float32)
        weightView[update] = newWeight

    def _RefineWithBilinearDepth(self, measured: np.ndarray, inside: np.ndarray, u: np.ndarray, v: np.ndarray, depthMm: np.ndarray) -> np.ndarray:
        """Overwrite `measured` with the interpolated depth where the four pixels share one surface."""
        height, width = depthMm.shape
        coherent = np.zeros(measured.shape, dtype=bool)
        selected = np.flatnonzero(inside)
        uu = u.reshape(-1)[selected]
        vv = v.reshape(-1)[selected]
        u0 = np.floor(uu).astype(np.int64)
        v0 = np.floor(vv).astype(np.int64)
        usable = (u0 >= 0) & (u0 + 1 < width) & (v0 >= 0) & (v0 + 1 < height)
        selected, uu, vv, u0, v0 = selected[usable], uu[usable], vv[usable], u0[usable], v0[usable]
        corners = np.stack([depthMm[v0, u0], depthMm[v0, u0 + 1], depthMm[v0 + 1, u0], depthMm[v0 + 1, u0 + 1]])
        onOneSurface = (corners.min(axis=0) > 0) & (corners.max(axis=0) - corners.min(axis=0) <= self.truncationMm)
        fu, fv = uu - u0, vv - v0
        top = corners[0] + fu * (corners[1] - corners[0])
        bottom = corners[2] + fu * (corners[3] - corners[2])
        blended = top + fv * (bottom - top)
        measured.reshape(-1)[selected[onOneSurface]] = blended[onOneSurface]
        coherent.reshape(-1)[selected[onOneSurface]] = True
        return coherent

    @staticmethod
    def _ObservationWeights(view: _View, inside: np.ndarray, row: np.ndarray, col: np.ndarray, update: np.ndarray) -> np.ndarray:
        """Weight of each updated voxel's observation; voxels whose pixel weight is 0 are dropped from `update`."""
        if view.weights is None:
            return np.ones(int(update.sum()))
        pixelWeight = np.zeros(update.shape, dtype=np.float64)
        pixelWeight[inside] = view.weights[row[inside], col[inside]]
        update &= pixelWeight > 0
        return pixelWeight[update]


def SampleRgb(rgb: np.ndarray, u: np.ndarray, v: np.ndarray, bilinear: np.ndarray) -> np.ndarray:
    """Colour at pixel coordinates (u, v) as float64: bilinear where `bilinear` is set, nearest elsewhere."""
    height, width = rgb.shape[:2]
    nearestCol = np.clip(np.rint(u), 0, width - 1).astype(np.int64)
    nearestRow = np.clip(np.rint(v), 0, height - 1).astype(np.int64)
    sampled = rgb[nearestRow, nearestCol].astype(np.float64)
    if bilinear.any():
        uu = np.clip(u[bilinear], 0, width - 1)
        vv = np.clip(v[bilinear], 0, height - 1)
        u0, v0 = np.floor(uu).astype(np.int64), np.floor(vv).astype(np.int64)
        u1, v1 = np.minimum(u0 + 1, width - 1), np.minimum(v0 + 1, height - 1)
        fu, fv = (uu - u0)[:, None], (vv - v0)[:, None]
        topLeft, topRight = rgb[v0, u0].astype(np.float64), rgb[v0, u1].astype(np.float64)
        bottomLeft, bottomRight = rgb[v1, u0].astype(np.float64), rgb[v1, u1].astype(np.float64)
        top = topLeft + fu * (topRight - topLeft)
        bottom = bottomLeft + fu * (bottomRight - bottomLeft)
        sampled[bilinear] = top + fv * (bottom - top)
    return sampled


def MergeVolumes(first: TsdfVolume, second: TsdfVolume) -> TsdfVolume:
    """Exact weighted merge: D = (w1 d1 + w2 d2) / (w1 + w2), the same for colour."""
    if first.grid != second.grid or first.truncationMm != second.truncationMm:
        raise ValueError("volumes must share the same grid and truncation")
    merged = TsdfVolume(first.grid, truncationMm=first.truncationMm)
    total = first.weight.astype(np.float64) + second.weight
    observed = total > 0
    safeTotal = np.where(observed, total, 1.0)
    weightedTsdf = first.weight * first.tsdf.astype(np.float64) + second.weight * second.tsdf
    merged.tsdf = np.where(observed, weightedTsdf / safeTotal, 1.0).astype(np.float32)
    colorBlend = (first.weight[..., None] * first.color + second.weight[..., None] * second.color) / safeTotal[..., None]
    merged.color = colorBlend.astype(np.float32)
    merged.weight = total.astype(np.float32)
    return merged
