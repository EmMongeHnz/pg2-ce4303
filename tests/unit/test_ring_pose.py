import numpy as np
import pytest

from capture_service.ring_pose import AngleDifferenceDeg, RingPoseEstimator, RotationErrorDeg
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
