import numpy as np

from astrea_common.rgbd_frame import Intrinsics
from astrea_common.tsdf import GridSpec, MergeVolumes, TsdfVolume
from sphere_scene import INTRINSICS, SMALL_GRID, AroundPoses, SphereDepthMm

CAMERA = Intrinsics(fx=200.0, fy=200.0, cx=80.0, cy=60.0, width=160, height=120)
PLANE_GRID = GridSpec(originMm=(-20.0, -20.0, 285.0), voxelMm=1.0, shape=(40, 40, 30))
TILT = np.radians(50.0)
IDENTITY = np.eye(3)
ORIGIN = np.zeros(3)


def TiltedPlaneDepthMm() -> np.ndarray:
    v, u = np.indices((CAMERA.height, CAMERA.width))
    ray = np.stack([(u - CAMERA.cx) / CAMERA.fx, (v - CAMERA.cy) / CAMERA.fy, np.ones(u.shape)], axis=-1)
    normal = np.array([0.0, -np.sin(TILT), np.cos(TILT)])
    return (300.0 * np.cos(TILT) / (ray @ normal)).astype(np.float32)


def AnalyticTsdf(volume: TsdfVolume) -> np.ndarray:
    x, y, z = np.meshgrid(*(volume.grid.AxisMm(axis) for axis in range(3)), indexing="ij")
    normal = np.array([0.0, -np.sin(TILT), np.cos(TILT)])
    surfaceZ = 300.0 * np.cos(TILT) / (x / z * normal[0] + y / z * normal[1] + normal[2])
    return np.minimum((surfaceZ - z) / volume.truncationMm, 1.0)


def SurfaceError(bilinear: bool) -> float:
    volume = TsdfVolume(PLANE_GRID)
    volume.Integrate(TiltedPlaneDepthMm(), CAMERA, IDENTITY, ORIGIN, bilinear=bilinear)
    expected = AnalyticTsdf(volume)
    nearSurface = (volume.weight > 0) & (np.abs(expected) < 0.5)
    assert nearSurface.sum() > 500
    return float(np.abs(volume.tsdf[nearSurface] - expected[nearSurface]).mean())


def test_bilinear_sampling_follows_a_tilted_surface_better_than_nearest_pixel():
    assert SurfaceError(bilinear=True) < 0.5 * SurfaceError(bilinear=False)


def test_bilinear_sampling_does_not_bridge_a_depth_jump():
    depth = np.full((CAMERA.height, CAMERA.width), 300.0, dtype=np.float32)
    depth[:, CAMERA.width // 2 :] = 360.0
    smooth, nearest = TsdfVolume(PLANE_GRID), TsdfVolume(PLANE_GRID)
    smooth.Integrate(depth, CAMERA, IDENTITY, ORIGIN, bilinear=True)
    nearest.Integrate(depth, CAMERA, IDENTITY, ORIGIN, bilinear=False)
    assert np.allclose(smooth.tsdf, nearest.tsdf, atol=1e-6)
    assert np.array_equal(smooth.weight, nearest.weight)


def GradientRgb(shift: int) -> np.ndarray:
    v, u = np.indices((INTRINSICS.height, INTRINSICS.width))
    return np.stack([(u * 255 // INTRINSICS.width + shift) % 256, v * 255 // INTRINSICS.height, np.full(u.shape, 90 + shift)], axis=-1).astype(np.uint8)


def WeightMap(depthMm: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.where(depthMm > 0, rng.uniform(0.2, 3.0, depthMm.shape), 0.0).astype(np.float32)


def FuseWeighted(indexedPoses) -> TsdfVolume:
    volume = TsdfVolume(SMALL_GRID)
    for index, (rotation, translation) in indexedPoses:
        depth = SphereDepthMm(rotation, translation)
        volume.Integrate(depth, INTRINSICS, rotation, translation, GradientRgb(index), weights=WeightMap(depth, index))
    return volume


def test_weighted_volumes_with_varying_colour_merge_exactly():
    indexed = list(enumerate(AroundPoses()))
    single = FuseWeighted(indexed)
    merged = MergeVolumes(FuseWeighted(indexed[::2]), FuseWeighted(indexed[1::2]))
    assert np.allclose(merged.weight, single.weight, rtol=1e-5)
    assert np.abs(merged.tsdf - single.tsdf).max() < 1e-4
    assert np.abs(merged.color - single.color).max() < 1e-2


def test_zero_weight_pixels_do_not_contribute():
    rotation, translation = AroundPoses()[0]
    depth = SphereDepthMm(rotation, translation)
    volume = TsdfVolume(SMALL_GRID)
    volume.Integrate(depth, INTRINSICS, rotation, translation, weights=np.zeros_like(depth))
    assert not volume.weight.any()


def test_colour_keeps_tracking_the_mean_after_many_observations():
    rotation, translation = AroundPoses()[0]
    depth = SphereDepthMm(rotation, translation)
    volume = TsdfVolume(SMALL_GRID)
    for index in range(100):
        shade = 0 if index < 50 else 100
        volume.Integrate(depth, INTRINSICS, rotation, translation, np.full((INTRINSICS.height, INTRINSICS.width, 3), shade, np.uint8))
    surface = (volume.weight > 0) & (np.abs(volume.tsdf) < 0.5)
    assert surface.sum() > 50
    assert np.allclose(volume.color[surface], 50.0, atol=1e-3)


def test_cube_grid_matches_the_default_grid():
    assert GridSpec.Cube(250.0, 250.0 / 192) == GridSpec.Default()


def test_grid_fitted_to_bounds_contains_the_box_with_margin():
    low, high = np.array([-30.0, -25.0, 0.0]), np.array([30.0, 25.0, 80.0])
    grid = GridSpec.FitToBounds(low, high, voxelMm=0.65, marginMm=8.0)
    for axis in range(3):
        axisValues = grid.AxisMm(axis)
        assert axisValues[0] <= low[axis] - 8.0 + 1e-9
        assert axisValues[-1] >= high[axis] + 8.0 - 1e-9
        assert axisValues[-1] - (high[axis] + 8.0) < 0.65 + 1e-9
    assert grid.memoryBytes == grid.voxelCount * 20
