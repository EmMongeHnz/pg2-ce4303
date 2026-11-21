"""Average of the poses measured while the turntable was still, which has less noise than any single frame."""

from __future__ import annotations

import numpy as np

from astrea_common.rotations import MeanRotation
from capture_service.ring_pose import RingPose


def AveragePose(poses: list[RingPose]) -> RingPose:
    markerIds = tuple(sorted({markerId for pose in poses for markerId in pose.markerIds}))
    return RingPose(
        rotation=MeanRotation([pose.rotation for pose in poses]),
        translationMm=np.mean([pose.translationMm for pose in poses], axis=0),
        markerIds=markerIds,
        reprojectionErrorPx=float(np.mean([pose.reprojectionErrorPx for pose in poses])),
    )
