import numpy as np

from astrea_common.point_cloud import BackProject
from astrea_common.rgbd_frame import Intrinsics, RgbdFrame

INTRINSICS = Intrinsics(fx=400.0, fy=400.0, cx=100.0, cy=80.0, width=320, height=240)


def Project(intrinsics, point):
    return intrinsics.fx * point[0] / point[2] + intrinsics.cx, intrinsics.fy * point[1] / point[2] + intrinsics.cy


def test_mirrored_intrinsics_follow_the_mirrored_image():
    point = np.array([0.05, -0.02, 0.4])
    mirroredPoint = point * np.array([-1.0, 1.0, 1.0])
    u, v = Project(INTRINSICS, point)
    mirroredU, mirroredV = Project(INTRINSICS.MirroredHorizontally(), mirroredPoint)
    assert np.isclose(mirroredU, INTRINSICS.width - 1 - u)
    assert np.isclose(mirroredV, v)


def test_frame_mirroring_flips_pixels():
    depth = np.arange(12, dtype=np.float32).reshape(3, 4)
    frame = RgbdFrame(0, np.zeros((6, 8, 3), np.uint8), depth, INTRINSICS.ScaledTo(8, 6))
    assert np.array_equal(frame.MirroredHorizontally().depthM, depth[:, ::-1])


def test_back_projection_of_a_flat_wall():
    depth = np.full((240, 320), 0.5, np.float32)
    depth[0, :] = 0.0
    points, indices = BackProject(depth, INTRINSICS)
    assert len(points) == 239 * 320 == len(indices)
    assert np.allclose(points[:, 2], 0.5)
    center = points[np.argmin(np.abs(indices - (80 * 320 + 100)))]
    assert np.allclose(center[:2], 0.0)
