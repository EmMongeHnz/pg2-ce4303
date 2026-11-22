import math

import numpy as np

from astrea_common.rotations import RotationAboutAxis
from capture_service.ring_pose import RotationErrorDeg
from capture_service.synthetic_ring import ViewPose
from coordinator.pose_refinement import CameraCentre, FitTurntable

VIEW_COUNT = 36


def TruePoses(elevationDeg: float = 35.0, distanceMm: float = 300.0, height: float = 40.0):
    return [ViewPose(az, elevationDeg, distanceMm, targetHeightMm=height).PlateToCamera() for az in np.arange(VIEW_COUNT) * 360.0 / VIEW_COUNT]


def Azimuths(poses):
    return np.array([math.atan2(CameraCentre(pose)[1], CameraCentre(pose)[0]) for pose in poses])


def test_perfect_poses_are_returned_unchanged():
    poses = TruePoses()
    fit = FitTurntable(poses)
    assert fit.applied
    for (rotation, translation), (refinedRotation, refinedTranslation) in zip(poses, fit.poses):
        assert RotationErrorDeg(refinedRotation, rotation) < 1e-4
        assert np.linalg.norm(refinedTranslation - translation) < 1e-6


def ViewingElevationDeg(pose) -> float:
    forward = pose[0].T @ np.array([0.0, 0.0, 1.0])
    return math.degrees(math.atan2(forward[2], math.hypot(forward[0], forward[1])))


def test_tilt_distance_and_translation_noise_are_reduced():
    truth = TruePoses()
    rng = np.random.default_rng(1)
    noisy = []
    for rotation, translation in truth:
        tilt = RotationAboutAxis(rotation[:, 0], math.radians(rng.normal(0.0, 1.5)))
        noisy.append((tilt @ rotation, translation + rng.normal(0.0, 1.5, 3)))
    fit = FitTurntable(noisy)
    assert fit.applied
    elevationBefore = np.mean([abs(ViewingElevationDeg(n) - ViewingElevationDeg(t)) for n, t in zip(noisy, truth)])
    elevationAfter = np.mean([abs(ViewingElevationDeg(r) - ViewingElevationDeg(t)) for r, t in zip(fit.poses, truth)])
    assert elevationAfter < 0.5 * elevationBefore
    radiusBefore = np.std([np.hypot(*CameraCentre(pose)[:2]) for pose in noisy])
    radiusAfter = np.std([np.hypot(*CameraCentre(pose)[:2]) for pose in fit.poses])
    assert radiusAfter < 0.2 * radiusBefore
    heightBefore = np.std([CameraCentre(pose)[2] for pose in noisy])
    heightAfter = np.std([CameraCentre(pose)[2] for pose in fit.poses])
    assert heightAfter < 0.2 * heightBefore


def test_azimuth_errors_are_left_as_measured():
    truth = TruePoses()
    rng = np.random.default_rng(2)
    measured = [(rotation @ RotationAboutAxis(np.array([0.0, 0.0, 1.0]), math.radians(rng.normal(0.0, 3.0))), translation) for rotation, translation in truth]
    fit = FitTurntable(measured)
    assert fit.applied
    assert np.allclose(Azimuths(fit.poses), Azimuths(measured), atol=math.radians(0.05))
    for (rotation, translation), (refinedRotation, refinedTranslation) in zip(measured, fit.poses):
        assert RotationErrorDeg(refinedRotation, rotation) < 0.05
        assert np.linalg.norm(refinedTranslation - translation) < 0.05


def test_off_centre_ring_is_tolerated():
    offset = np.array([8.0, -5.0, 0.0])
    shifted = [(rotation, translation + rotation @ offset) for rotation, translation in TruePoses()]
    fit = FitTurntable(shifted)
    assert fit.applied
    for (rotation, translation), (refinedRotation, refinedTranslation) in zip(shifted, fit.poses):
        assert RotationErrorDeg(refinedRotation, rotation) < 1e-3
        assert np.linalg.norm(refinedTranslation - translation) < 1e-4


def test_moving_camera_is_detected_and_skipped():
    rng = np.random.default_rng(3)
    wandering = [ViewPose(az, 35.0, 300.0 + rng.uniform(-40, 40), targetHeightMm=40.0 + rng.uniform(-40, 40)).PlateToCamera() for az in np.arange(VIEW_COUNT) * 10.0]
    fit = FitTurntable(wandering)
    assert not fit.applied
    assert "moved" in fit.message


def test_few_views_or_a_short_arc_are_skipped():
    assert not FitTurntable(TruePoses()[:4]).applied
    shortArc = [ViewPose(az, 35.0, 300.0).PlateToCamera() for az in np.linspace(0.0, 40.0, 10)]
    assert "span" in FitTurntable(shortArc).message
