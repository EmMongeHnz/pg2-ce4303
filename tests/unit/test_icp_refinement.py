import math

import numpy as np
import pytest

pytest.importorskip("scipy")
from astrea_common.mesh import TriangleMesh  # noqa: E402
from astrea_common.rotations import RotationAboutAxis  # noqa: E402
from astrea_common.view_package import ViewPackage  # noqa: E402
from astrea_common.view_preprocess import PreprocessParams  # noqa: E402
from capture_service.ring_pose import RotationErrorDeg  # noqa: E402
from capture_service.synthetic_depth import MeshDepthRenderer  # noqa: E402
from capture_service.synthetic_ring import ViewPose  # noqa: E402
from coordinator.pose_refinement import RefineAzimuthWithIcp, RefineWithIcp  # noqa: E402
from coordinator.reconstruct import ReconstructSettings, RegistrationMesh  # noqa: E402
from sphere_scene import INTRINSICS, UvSphereMesh  # noqa: E402

HEIGHT_MM = 40.0
VIEW_COUNT = 24
PARAMS = PreprocessParams()
SETTINGS = ReconstructSettings(voxelMm=1.5)


def LumpyMesh() -> TriangleMesh:
    sphere = UvSphereMesh(rings=70, segments=140)
    x, y, z = sphere.vertices.T
    radius = np.linalg.norm(sphere.vertices, axis=1)
    theta = np.arccos(np.clip(z / radius, -1.0, 1.0))
    phi = np.arctan2(y, x)
    scale = 1.0 + 0.25 * np.sin(2 * theta) * np.cos(3 * phi) + 0.2 * np.sin(theta) * np.cos(phi)
    return TriangleMesh(sphere.vertices * scale[:, None] + np.array([0.0, 0.0, HEIGHT_MM]), sphere.faces)


def MakeViews(azimuthErrorDeg: float, seed: int = 0):
    renderer = MeshDepthRenderer(LumpyMesh(), INTRINSICS, pointSpacingMm=0.6)
    rng = np.random.default_rng(seed)
    truth, measured = [], []
    for index in range(VIEW_COUNT):
        rotation, translation = ViewPose(index * 360.0 / VIEW_COUNT, 35.0, 300.0, targetHeightMm=HEIGHT_MM).PlateToCamera()
        depth = renderer.Render(rotation, translation).depthMm
        noisyRotation = rotation @ RotationAboutAxis(np.array([0.0, 0.0, 1.0]), math.radians(rng.normal(0.0, azimuthErrorDeg)))
        rgb = np.zeros((INTRINSICS.height, INTRINSICS.width, 3), np.uint8)
        truth.append(rotation)
        measured.append(ViewPackage(index, rgb, depth / 1000.0, INTRINSICS, noisyRotation, translation, 0.0, 35.0, 0.0))
    return truth, measured


def MeanRotationError(views, truth) -> float:
    return float(np.mean([RotationErrorDeg(view.rotation, rotation) for view, rotation in zip(views, truth)]))


def test_icp_pulls_azimuth_errors_back_towards_the_truth():
    truth, measured = MakeViews(azimuthErrorDeg=3.0)
    refined, summaries = RefineWithIcp(measured, PARAMS, lambda views: RegistrationMesh(views, PARAMS, SETTINGS))
    before, after = MeanRotationError(measured, truth), MeanRotationError(refined, truth)
    assert len(summaries) == 3
    assert after < 0.5 * before
    assert after < 1.2


def test_icp_leaves_correct_poses_almost_untouched():
    truth, exact = MakeViews(azimuthErrorDeg=0.0)
    refined, _ = RefineWithIcp(exact, PARAMS, lambda views: RegistrationMesh(views, PARAMS, SETTINGS))
    assert MeanRotationError(refined, truth) < 0.3


def test_azimuth_icp_turns_views_about_the_axis_without_moving_cameras_off_their_orbit():
    truth, measured = MakeViews(azimuthErrorDeg=3.0)
    orbit = (np.array([0.0, 0.0, 1.0]), np.zeros(3))
    refined, summaries = RefineAzimuthWithIcp(measured, [orbit] * len(measured), PARAMS, lambda views: RegistrationMesh(views, PARAMS, SETTINGS))
    before, after = MeanRotationError(measured, truth), MeanRotationError(refined, truth)
    assert after < 0.5 * before
    radiiBefore = [np.hypot(*(-(view.rotation.T @ view.translationMm))[:2]) for view in measured]
    radiiAfter = [np.hypot(*(-(view.rotation.T @ view.translationMm))[:2]) for view in refined]
    assert np.allclose(radiiBefore, radiiAfter, atol=1e-6)
    assert all(summary["meanCameraMoveMm"] < 15.0 for summary in summaries)


def test_azimuth_icp_leaves_views_without_an_orbit_alone():
    _, measured = MakeViews(azimuthErrorDeg=3.0)
    refined, _ = RefineAzimuthWithIcp(measured, [None] * len(measured), PARAMS, lambda views: RegistrationMesh(views, PARAMS, SETTINGS))
    assert all(np.array_equal(a.rotation, b.rotation) for a, b in zip(measured, refined))
