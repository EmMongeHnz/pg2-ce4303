import numpy as np

from astrea_common.rgbd_frame import Intrinsics
from astrea_common.view_package import ViewPackage
from astrea_common.view_preprocess import PreprocessParams, PreprocessView, RemoveSmallComponents, RemoveUnsupportedPixels

INTRINSICS = Intrinsics(fx=200.0, fy=200.0, cx=80.0, cy=60.0, width=160, height=120)
PLATE_HEIGHT_CAMERA_MM = 300.0
LOOKING_DOWN = np.diag([1.0, -1.0, -1.0])
PARAMS = PreprocessParams()


def TopDownView(surfaceHeightMm: float) -> ViewPackage:
    """Camera 300 mm above the plate centre looking straight down at a flat surface at `surfaceHeightMm`."""
    depthM = np.full((INTRINSICS.height, INTRINSICS.width), (PLATE_HEIGHT_CAMERA_MM - surfaceHeightMm) / 1000.0, dtype=np.float32)
    return ViewPackage(0, np.zeros((INTRINSICS.height, INTRINSICS.width, 3), np.uint8), depthM, INTRINSICS, LOOKING_DOWN, np.array([0.0, 0.0, PLATE_HEIGHT_CAMERA_MM]), 0.0, 90.0, 0.0)


def test_plate_pixels_are_removed():
    assert not PreprocessView(TopDownView(0.0), PARAMS).any()


def test_surface_above_the_plate_is_kept_inside_the_cylinder_only():
    cleaned = PreprocessView(TopDownView(50.0), PARAMS)
    rows, cols = np.indices(cleaned.shape)
    radiusMm = np.hypot((cols - INTRINSICS.cx) / INTRINSICS.fx, (rows - INTRINSICS.cy) / INTRINSICS.fy) * 250.0
    assert cleaned[60, 80] > 0
    assert np.all(cleaned[radiusMm > PARAMS.cylinderRadiusMm + 1.0] == 0)
    assert np.all(cleaned[radiusMm < PARAMS.cylinderRadiusMm - 2.0] > 0)


def test_points_beyond_the_depth_range_are_removed():
    far = TopDownView(50.0)
    far.depthM[:] = 0.6
    assert not PreprocessView(far, PARAMS).any()


def test_isolated_flying_pixel_is_removed_without_touching_its_neighbours():
    depth = np.full((20, 20), 250.0, dtype=np.float32)
    depth[10, 10] = 400.0
    cleaned = RemoveUnsupportedPixels(depth, 8.0, 3)
    assert cleaned[10, 10] == 0
    assert cleaned[9, 10] == 250.0 and cleaned[11, 11] == 250.0


def test_small_components_are_removed_and_large_ones_kept():
    depth = np.zeros((40, 40), dtype=np.float32)
    depth[2:4, 2:4] = 250.0
    depth[10:30, 10:30] = 250.0
    cleaned = RemoveSmallComponents(depth, 30)
    assert not cleaned[2:4, 2:4].any()
    assert np.all(cleaned[10:30, 10:30] == 250.0)


def test_preprocessing_is_deterministic():
    view = TopDownView(50.0)
    assert np.array_equal(PreprocessView(view, PARAMS), PreprocessView(view, PARAMS))
