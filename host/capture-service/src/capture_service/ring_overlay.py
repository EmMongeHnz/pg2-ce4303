"""Ring pose tracking drawn over live RGB frames, for the stream viewer.

Shows the detected markers, the plate axes and a status block: azimuth, elevation,
distance, markers used, reprojection error, depth-vs-pose residual and how many of
the recent frames produced a pose. When no pose comes out the block turns red and
shows how many markers were detected, which tells occlusion (few markers) apart from
a rejected solution (enough markers, implausible pose).
"""

from __future__ import annotations

from collections import deque

import cv2
import numpy as np

from astrea_common.aruco_ring import RingLayout
from astrea_common.rgbd_frame import RgbdFrame
from capture_service.depth_check import MarkerDepthResidualsMm
from capture_service.frame_dump import RgbAnnotation
from capture_service.ring_pose import RingPose, RingPoseEstimator

GREEN = (0, 220, 0)
RED = (0, 0, 255)
AXIS_LENGTH_MM = 40
HISTORY_FRAMES = 60


class RingOverlay:
    def __init__(self, layout: RingLayout) -> None:
        self.layout = layout
        self._estimator: RingPoseEstimator | None = None
        self._poseHistory: deque[bool] = deque(maxlen=HISTORY_FRAMES)

    def Render(self, frame: RgbdFrame) -> RgbAnnotation:
        """Return `frame` with the ring drawn on it, plus the status text to show upright."""
        if self._estimator is None:
            self._estimator = RingPoseEstimator(self.layout, frame.rgbIntrinsics.Matrix())
        bgr = frame.Bgr()
        detections = self._estimator.DetectMarkers(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        pose = self._estimator.EstimateFromDetections(detections)
        self._poseHistory.append(pose is not None)
        for corners in detections.values():
            cv2.polylines(bgr, [np.round(corners).astype(np.int32)], True, GREEN if pose else RED, 2)
        if pose is None:
            return RgbAnnotation(bgr, self._NoPoseLines(len(detections)), RED)
        self._DrawAxes(bgr, pose)
        return RgbAnnotation(bgr, self._PoseLines(frame, pose), GREEN)

    def _DrawAxes(self, bgr: np.ndarray, pose: RingPose) -> None:
        rvec, _ = cv2.Rodrigues(pose.rotation)
        cv2.drawFrameAxes(bgr, self._estimator.cameraMatrix, self._estimator.distortion, rvec, pose.translationMm, AXIS_LENGTH_MM)

    def _PoseLines(self, frame: RgbdFrame, pose: RingPose) -> list[str]:
        residuals = MarkerDepthResidualsMm(frame, pose, self.layout)
        residualText = f"{np.median(residuals):+.1f} mm" if residuals else "n/a"
        return [
            f"az {pose.azimuthDeg:6.2f}  el {pose.elevationDeg:5.1f}  dist {pose.distanceMm:5.0f} mm",
            f"markers {len(pose.markerIds)}/{self.layout.markerCount}  reproj {pose.reprojectionErrorPx:.2f} px",
            f"depth-vs-pose {residualText}  pose {self._PoseRatioPercent():.0f} %",
        ]

    def _NoPoseLines(self, detectedMarkers: int) -> list[str]:
        return [
            f"NO POSE  markers {detectedMarkers}/{self.layout.markerCount}",
            f"pose {self._PoseRatioPercent():.0f} % of last {len(self._poseHistory)} frames",
        ]

    def _PoseRatioPercent(self) -> float:
        return 100.0 * sum(self._poseHistory) / len(self._poseHistory)
