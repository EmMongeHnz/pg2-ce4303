import numpy as np
import pytest

from astrea_common.tsdf import MergeVolumes, TsdfVolume
from sphere_scene import COLOR, INTRINSICS, SMALL_GRID, AroundPoses, SphereDepthMm


def Integrate(volume, poses):
    rgb = np.full((INTRINSICS.height, INTRINSICS.width, 3), COLOR, dtype=np.uint8)
    for rotation, translation in poses:
        volume.Integrate(SphereDepthMm(rotation, translation), INTRINSICS, rotation, translation, rgb)


@pytest.fixture(scope="module")
def fullVolume():
    volume = TsdfVolume(SMALL_GRID)
    Integrate(volume, AroundPoses())
    return volume


def test_unobserved_voxels_keep_the_initial_value(fullVolume):
    unobserved = fullVolume.weight == 0
    assert unobserved.any()
    assert np.all(fullVolume.tsdf[unobserved] == 1.0)


def test_surface_voxels_carry_the_observed_colour(fullVolume):
    nearSurface = (fullVolume.weight > 0) & (np.abs(fullVolume.tsdf) < 0.5)
    assert nearSurface.sum() > 100
    assert np.all(fullVolume.color[nearSurface] == COLOR)


def test_merging_partial_volumes_equals_integrating_all_views(fullVolume):
    poses = AroundPoses()
    first, second = TsdfVolume(SMALL_GRID), TsdfVolume(SMALL_GRID)
    Integrate(first, poses[::2])
    Integrate(second, poses[1::2])
    merged = MergeVolumes(first, second)
    assert np.array_equal(merged.weight, fullVolume.weight)
    assert np.abs(merged.tsdf - fullVolume.tsdf).max() < 1e-5
    assert np.abs(merged.color.astype(int) - fullVolume.color.astype(int)).max() <= 1
