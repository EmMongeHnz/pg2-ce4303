import numpy as np
import pytest

from capture_service.ring_pose import AngleDifferenceDeg, RingPose, RingPoseEstimator, RotationErrorDeg
from capture_service.synthetic_ring import SceneOptions, SyntheticRingRenderer, ViewPose

GATE_AZIMUTH_DEG = 1.0


@pytest.fixture(scope="module")
def renderer():
    return SyntheticRingRenderer()


@pytest.fixture(scope="module")
def estimator(renderer):
    return RingPoseEstimator(renderer.layout, renderer.camera.Matrix())


@pytest.mark.parametrize("azimuth", range(0, 360, 30))
@pytest.mark.parametrize("elevation", [25, 45])
def test_pose_matches_ground_truth_with_object_occlusion(renderer, estimator, azimuth, elevation):
    pose = ViewPose(azimuth, elevation, 350)
    estimate = estimator.Estimate(renderer.Render(pose, SceneOptions(seed=azimuth)))
    assert estimate is not None
    rotation, translation = pose.PlateToCamera()
    assert len(estimate.markerIds) < renderer.layout.markerCount
    assert abs(AngleDifferenceDeg(estimate.azimuthDeg, azimuth)) < GATE_AZIMUTH_DEG
    assert RotationErrorDeg(estimate.rotation, rotation) < GATE_AZIMUTH_DEG
    assert np.linalg.norm(estimate.translationMm - translation) < 3.0
    assert abs(estimate.elevationDeg - elevation) < GATE_AZIMUTH_DEG
    assert abs(estimate.distanceMm - 350) < 3.0


def test_no_pose_without_markers(estimator):
    assert estimator.Estimate(np.full((480, 640), 128, np.uint8)) is None


def test_displaced_marker_corners_are_rejected(renderer, estimator):
    pose = ViewPose(60, 35, 350)
    detections = estimator.DetectMarkers(renderer.Render(pose, SceneOptions(objectRadiusMm=0.0)))
    corrupted = dict(detections)
    victim = sorted(corrupted)[0]
    corrupted[victim] = corrupted[victim] + np.array([[9.0, 0.0], [9.0, 0.0], [0.0, 0.0], [0.0, 0.0]])
    estimate = estimator.EstimateFromDetections(corrupted)
    assert victim not in estimate.markerIds
    assert abs(AngleDifferenceDeg(estimate.azimuthDeg, 60)) < 0.3


def test_static_pose_is_stable_under_sensor_noise(renderer, estimator):
    pose = ViewPose(140, 35, 350)
    azimuths = [
        estimator.Estimate(renderer.Render(pose, SceneOptions(noiseSigma=4.0, seed=seed))).azimuthDeg
        for seed in range(8)
    ]
    assert np.std(azimuths) < 0.1


def test_pose_is_rejected_when_every_marker_disagrees(renderer, estimator):
    pose = ViewPose(60, 35, 350)
    detections = estimator.DetectMarkers(renderer.Render(pose, SceneOptions(objectRadiusMm=0.0)))
    rng = np.random.default_rng(7)
    scrambled = {i: corners + rng.normal(0.0, 25.0, corners.shape) for i, corners in detections.items()}
    assert estimator.EstimateFromDetections(scrambled) is None


def test_pose_with_camera_under_the_plate_is_not_plausible():
    belowPlate = RingPose(np.eye(3), np.array([0.0, 0.0, 300.0]), (0, 1, 2), 0.5)
    assert belowPlate.elevationDeg < 0.0
    assert not RingPoseEstimator._IsPlausible(belowPlate)
