import numpy as np

from astrea_common.rgbd_frame import Intrinsics
from astrea_common.view_preprocess import PreprocessParams, ComputeViewWeights

CAMERA = Intrinsics(fx=200.0, fy=200.0, cx=80.0, cy=60.0, width=160, height=120)
PARAMS = PreprocessParams()


def FlatDepth(depthMm: float) -> np.ndarray:
    return np.full((CAMERA.height, CAMERA.width), depthMm, dtype=np.float32)


def test_frontal_surface_at_the_reference_depth_has_unit_weight():
    weights = ComputeViewWeights(FlatDepth(300.0), CAMERA, PARAMS)
    assert np.allclose(weights[5:-5, 5:-5].mean(), 1.0, atol=0.05)


def test_far_surfaces_weigh_less_than_near_ones():
    near = ComputeViewWeights(FlatDepth(250.0), CAMERA, PARAMS)[60, 80]
    far = ComputeViewWeights(FlatDepth(400.0), CAMERA, PARAMS)[60, 80]
    assert near > 1.0 > far


def test_tilted_surface_weighs_less_than_a_frontal_one():
    v, u = np.indices((CAMERA.height, CAMERA.width))
    ray = np.stack([(u - CAMERA.cx) / CAMERA.fx, (v - CAMERA.cy) / CAMERA.fy, np.ones(u.shape)], axis=-1)
    angle = np.radians(70.0)
    normal = np.array([0.0, -np.sin(angle), np.cos(angle)])
    tilted = (300.0 * np.cos(angle) / (ray @ normal)).astype(np.float32)
    assert ComputeViewWeights(tilted, CAMERA, PARAMS)[60, 80] < 0.5 * ComputeViewWeights(FlatDepth(300.0), CAMERA, PARAMS)[60, 80]


def test_missing_depth_has_zero_weight_and_depth_jumps_get_the_minimum_cosine():
    depth = FlatDepth(300.0)
    depth[:, 80:] = 380.0
    depth[10, 10] = 0.0
    weights = ComputeViewWeights(depth, CAMERA, PARAMS)
    assert weights[10, 10] == 0.0
    assert weights[60, 79] < 0.2 and weights[60, 80] < 0.2


def test_weights_are_deterministic():
    depth = FlatDepth(320.0)
    assert np.array_equal(ComputeViewWeights(depth, CAMERA, PARAMS), ComputeViewWeights(depth, CAMERA, PARAMS))
