"""Blocks cut from a volume and folded into another reproduce the exact weighted merge."""

import numpy as np
import pytest

from astrea_common.tsdf import GridSpec, MergeVolumes, TsdfVolume
from astrea_common.volume_blocks import DEFAULT_TILE_VOXELS, VolumeBlock, IterateBlocks, MergeBlockInto

GRID = GridSpec((0.0, 0.0, 0.0), 1.0, (40, 33, 20))


def RandomVolume(seed: int, density: float = 0.4) -> TsdfVolume:
    rng = np.random.default_rng(seed)
    volume = TsdfVolume(GRID)
    observed = rng.random(GRID.shape) < density
    volume.weight[observed] = rng.uniform(0.1, 5.0, observed.sum()).astype(np.float32)
    volume.tsdf[observed] = rng.uniform(-1.0, 1.0, observed.sum()).astype(np.float32)
    volume.color[observed] = rng.uniform(0, 255, (observed.sum(), 3)).astype(np.float32)
    return volume


def Rebuild(volume: TsdfVolume, tileVoxels: int) -> TsdfVolume:
    rebuilt = TsdfVolume(GRID)
    for block in IterateBlocks(volume, tileVoxels):
        MergeBlockInto(rebuilt, block)
    return rebuilt


def test_blocks_of_a_volume_rebuild_it_exactly():
    volume = RandomVolume(1)
    rebuilt = Rebuild(volume, 8)
    np.testing.assert_array_equal(rebuilt.weight, volume.weight)
    np.testing.assert_array_equal(rebuilt.tsdf, volume.tsdf)
    np.testing.assert_array_equal(rebuilt.color, volume.color)


def test_only_tiles_with_weight_are_produced():
    volume = TsdfVolume(GRID)
    volume.weight[5, 5, 5] = 1.0
    blocks = list(IterateBlocks(volume, 8))
    assert len(blocks) == 1 and blocks[0].startIndex == (0, 0, 0)


def test_empty_volume_has_no_blocks():
    assert list(IterateBlocks(TsdfVolume(GRID))) == []


def test_edge_tiles_are_cropped_to_the_grid():
    volume = RandomVolume(2, density=1.0)
    shapes = {block.startIndex: block.shape for block in IterateBlocks(volume, 16)}
    assert shapes[(32, 32, 16)] == (8, 1, 4)
    assert sum(np.prod(shape) for shape in shapes.values()) == np.prod(GRID.shape)


def test_merging_blocks_matches_merge_volumes():
    first, second = RandomVolume(3), RandomVolume(4)
    expected = MergeVolumes(first, second)
    merged = Rebuild(first, DEFAULT_TILE_VOXELS)
    for block in IterateBlocks(second, 8):
        MergeBlockInto(merged, block)
    np.testing.assert_allclose(merged.weight, expected.weight, rtol=1e-6)
    assert np.abs(merged.tsdf - expected.tsdf).max() < 1e-5
    assert np.abs(merged.color - expected.color).max() < 1e-3


def test_unobserved_voxels_keep_the_empty_value():
    merged = TsdfVolume(GRID)
    block = VolumeBlock((0, 0, 0), np.full((4, 4, 4), 0.3, np.float32), np.zeros((4, 4, 4), np.float32), np.zeros((4, 4, 4, 3), np.float32))
    MergeBlockInto(merged, block)
    assert np.all(merged.tsdf == 1.0) and np.all(merged.weight == 0)


def test_block_outside_the_grid_is_rejected():
    block = VolumeBlock((38, 0, 0), np.zeros((4, 4, 4), np.float32), np.ones((4, 4, 4), np.float32), np.zeros((4, 4, 4, 3), np.float32))
    with pytest.raises(ValueError):
        MergeBlockInto(TsdfVolume(GRID), block)
    negative = VolumeBlock((-1, 0, 0), np.zeros((4, 4, 4), np.float32), np.ones((4, 4, 4), np.float32), np.zeros((4, 4, 4, 3), np.float32))
    with pytest.raises(ValueError):
        MergeBlockInto(TsdfVolume(GRID), negative)


def test_invalid_tile_size_is_rejected():
    with pytest.raises(ValueError):
        list(IterateBlocks(TsdfVolume(GRID), 0))
