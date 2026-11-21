"""Average of the poses measured while the turntable was still, which has less noise than any single frame."""

from __future__ import annotations

import numpy as np

from capture_service.ring_pose import RingPose


def MeanRotation(rotations: list[np.ndarray]) -> np.ndarray:
    """Chordal L2 mean: the rotation closest to the arithmetic mean of the matrices."""
    left, _, right = np.linalg.svd(np.sum(rotations, axis=0))
    correction = np.diag([1.0, 1.0, np.linalg.det(left @ right)])
    return left @ correction @ right


def AveragePose(poses: list[RingPose]) -> RingPose:
    markerIds = tuple(sorted({markerId for pose in poses for markerId in pose.markerIds}))
    return RingPose(
        rotation=MeanRotation([pose.rotation for pose in poses]),
        translationMm=np.mean([pose.translationMm for pose in poses], axis=0),
        markerIds=markerIds,
        reprojectionErrorPx=float(np.mean([pose.reprojectionErrorPx for pose in poses])),
    )
