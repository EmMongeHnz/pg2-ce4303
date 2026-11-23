"""Sparse blocks of a TSDF volume: what a worker sends back and how the coordinator folds it in.

A volume is cut into cubic tiles and only tiles holding some weight travel. Folding a block into another
volume uses the same weighted average as `MergeVolumes` (`D = (w1 d1 + w2 d2) / (w1 + w2)`, also for
colour), computed in float64 and stored as float32, so partial volumes built from disjoint sets of views
merge to the single-node result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np

from astrea_common.tsdf import TsdfVolume

DEFAULT_TILE_VOXELS = 32


@dataclass(frozen=True)
class VolumeBlock:
    """A box of voxels at `startIndex`; `tsdf` and `weight` are (a, b, c), `color` is (a, b, c, 3), all float32."""

    startIndex: tuple[int, int, int]
    tsdf: np.ndarray
    weight: np.ndarray
    color: np.ndarray

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.tsdf.shape


def _BlockSlices(startIndex: tuple[int, int, int], shape: tuple[int, int, int]) -> tuple[slice, slice, slice]:
    return tuple(slice(start, start + size) for start, size in zip(startIndex, shape))


def IterateBlocks(volume: TsdfVolume, tileVoxels: int = DEFAULT_TILE_VOXELS) -> Iterator[VolumeBlock]:
    """Yields a copy of every tile of `volume` that has weight > 0, one at a time to keep memory flat."""
    if tileVoxels < 1:
        raise ValueError("tileVoxels must be positive")
    shape = volume.grid.shape
    for x in range(0, shape[0], tileVoxels):
        for y in range(0, shape[1], tileVoxels):
            for z in range(0, shape[2], tileVoxels):
                tile = (slice(x, x + tileVoxels), slice(y, y + tileVoxels), slice(z, z + tileVoxels))
                if volume.weight[tile].any():
                    yield VolumeBlock((x, y, z), volume.tsdf[tile].copy(), volume.weight[tile].copy(), volume.color[tile].copy())


def MergeBlockInto(volume: TsdfVolume, block: VolumeBlock) -> None:
    """Folds `block` into `volume` in place with the exact weighted average."""
    region = _BlockSlices(block.startIndex, block.shape)
    if any(start < 0 or stop > limit for (start, stop), limit in zip(((s.start, s.stop) for s in region), volume.grid.shape)):
        raise ValueError(f"block at {block.startIndex} with shape {block.shape} does not fit in a grid of {volume.grid.shape}")
    weightHere = volume.weight[region].astype(np.float64)
    weightThere = block.weight.astype(np.float64)
    total = weightHere + weightThere
    observed = total > 0
    safeTotal = np.where(observed, total, 1.0)
    blendedTsdf = (weightHere * volume.tsdf[region] + weightThere * block.tsdf) / safeTotal
    blendedColor = (weightHere[..., None] * volume.color[region] + weightThere[..., None] * block.color) / safeTotal[..., None]
    volume.tsdf[region] = np.where(observed, blendedTsdf, volume.tsdf[region]).astype(np.float32)
    volume.color[region] = blendedColor.astype(np.float32)
    volume.weight[region] = total.astype(np.float32)
