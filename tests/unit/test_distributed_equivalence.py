"""M4 invariant checked early: preprocessing is per view, so splitting views across workers and merging
the partial volumes gives the same volume as fusing every view in one node."""

import numpy as np

from astrea_common.view_package import ViewPackage
from astrea_common.view_preprocess import PreprocessParams, PreprocessView
from astrea_common.tsdf import MergeVolumes, TsdfVolume
from capture_service.synthetic_ring import PlaneDepthMapM, ViewPose
from sphere_scene import COLOR, INTRINSICS, SMALL_GRID, SPHERE_RADIUS_MM, SphereDepthMm

DISTANCE_MM = 300.0
WORKERS = 3


def SphereOnPlateView(index: int, azimuthDeg: float, elevationDeg: float) -> ViewPackage:
    pose = ViewPose(azimuthDeg, elevationDeg, DISTANCE_MM)
    rotation, translation = pose.PlateToCamera()
    sphereMm = SphereDepthMm(rotation, translation)
    planeMm = PlaneDepthMapM(pose, INTRINSICS.Matrix(), INTRINSICS.width, INTRINSICS.height) * 1000.0
    depthMm = np.where((sphereMm > 0) & ((planeMm == 0) | (sphereMm < planeMm)), sphereMm, planeMm)
    rgb = np.full((INTRINSICS.height, INTRINSICS.width, 3), COLOR, dtype=np.uint8)
    return ViewPackage(index, rgb, depthMm / 1000.0, INTRINSICS, rotation, translation, azimuthDeg, elevationDeg, 0.0)


def Views() -> list[ViewPackage]:
    return [SphereOnPlateView(i, az, el) for i, (el, az) in enumerate((el, az) for el in (30.0, 55.0) for az in range(0, 360, 30))]


def Fuse(views: list[ViewPackage]) -> TsdfVolume:
    volume = TsdfVolume(SMALL_GRID)
    for view in views:
        volume.Integrate(PreprocessView(view, PreprocessParams()), view.intrinsics, view.rotation, view.translationMm, view.rgb)
    return volume


def test_split_and_merged_volumes_equal_single_node_fusion():
    views = Views()
    single = Fuse(views)
    parts = [Fuse(views[worker::WORKERS]) for worker in range(WORKERS)]
    merged = parts[0]
    for part in parts[1:]:
        merged = MergeVolumes(merged, part)
    assert np.array_equal(merged.weight, single.weight)
    assert np.abs(merged.tsdf - single.tsdf).max() < 1e-5
    assert np.array_equal(merged.color, single.color)


def test_plate_does_not_enter_the_volume():
    volume = Fuse(Views())
    x, y, z = np.meshgrid(*(SMALL_GRID.AxisMm(axis) for axis in range(3)), indexing="ij")
    surface = (volume.weight > 0) & (np.abs(volume.tsdf) < 0.5)
    assert surface.sum() > 100
    assert np.all(np.sqrt(x**2 + y**2 + z**2)[surface] < SPHERE_RADIUS_MM + 2 * SMALL_GRID.voxelMm)
