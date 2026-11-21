import math

import numpy as np

from capture_service.pose_average import AveragePose, MeanRotation
from capture_service.ring_pose import RingPose, RotationErrorDeg
from capture_service.synthetic_ring import ViewPose


def RotationAboutAxis(axis: np.ndarray, angleRad: float) -> np.ndarray:
    axis = axis / np.linalg.norm(axis)
    skew = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(angleRad) * skew + (1 - math.cos(angleRad)) * skew @ skew


def NoisyPoses(count: int, rotationSigmaDeg: float, translationSigmaMm: float, seed: int = 0):
    rotation, translation = ViewPose(45.0, 35.0, 300.0).PlateToCamera()
    rng = np.random.default_rng(seed)
    poses = []
    for _ in range(count):
        noise = RotationAboutAxis(rng.normal(size=3), math.radians(abs(rng.normal(0.0, rotationSigmaDeg))))
        poses.append(RingPose(noise @ rotation, translation + rng.normal(0.0, translationSigmaMm, 3), (0, 1, 2), 0.4))
    return rotation, translation, poses


def test_average_of_identical_poses_is_that_pose():
    rotation, translation, poses = NoisyPoses(5, 0.0, 0.0)
    averaged = AveragePose(poses)
    assert np.allclose(averaged.rotation, rotation)
    assert np.allclose(averaged.translationMm, translation)


def test_averaging_reduces_pose_noise():
    rotation, translation, poses = NoisyPoses(40, 0.5, 1.0)
    averaged = AveragePose(poses)
    singleRotationError = np.mean([RotationErrorDeg(pose.rotation, rotation) for pose in poses])
    singleTranslationError = np.mean([np.linalg.norm(pose.translationMm - translation) for pose in poses])
    assert RotationErrorDeg(averaged.rotation, rotation) < 0.5 * singleRotationError
    assert np.linalg.norm(averaged.translationMm - translation) < 0.5 * singleTranslationError


def test_mean_rotation_is_a_proper_rotation():
    _, _, poses = NoisyPoses(10, 2.0, 0.0)
    mean = MeanRotation([pose.rotation for pose in poses])
    assert np.allclose(mean @ mean.T, np.eye(3), atol=1e-9)
    assert np.isclose(np.linalg.det(mean), 1.0)


def test_marker_ids_are_the_union_and_error_is_the_mean():
    first = RingPose(np.eye(3), np.zeros(3), (0, 1), 0.2)
    second = RingPose(np.eye(3), np.zeros(3), (1, 2), 0.6)
    averaged = AveragePose([first, second])
    assert averaged.markerIds == (0, 1, 2)
    assert np.isclose(averaged.reprojectionErrorPx, 0.4)
