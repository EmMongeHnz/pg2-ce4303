"""Turntable pose from the ArUco ring (spike 0b).

Every visible marker contributes its four corners to a single solvePnP, so the
pose stays defined while the object or a hand hides part of the ring.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from astrea_common.aruco_ring import RingLayout

MIN_MARKERS = 2
OUTLIER_MIN_PX = 1.0
OUTLIER_FACTOR = 3.0
MAX_RMS_ERROR_PX = 3.0
MIN_ELEVATION_DEG = 0.0


@dataclass(frozen=True)
class RingPose:
    """Plate→camera transform: `xCamera = rotation @ xPlate + translationMm`."""

    rotation: np.ndarray
    translationMm: np.ndarray
    markerIds: tuple[int, ...]
    reprojectionErrorPx: float

    @property
    def cameraPositionMm(self) -> np.ndarray:
        return -self.rotation.T @ self.translationMm

    @property
    def azimuthDeg(self) -> float:
        """Viewing angle around the turntable axis, in [0, 360). Turning the plate changes it."""
        x, y, _ = self.cameraPositionMm
        return math.degrees(math.atan2(y, x)) % 360.0

    @property
    def elevationDeg(self) -> float:
        x, y, z = self.cameraPositionMm
        return math.degrees(math.atan2(z, math.hypot(x, y)))

    @property
    def distanceMm(self) -> float:
        return float(np.linalg.norm(self.cameraPositionMm))


def AngleDifferenceDeg(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0


def RotationErrorDeg(estimated: np.ndarray, truth: np.ndarray) -> float:
    cosine = (np.trace(estimated @ truth.T) - 1.0) / 2.0
    return math.degrees(math.acos(float(np.clip(cosine, -1.0, 1.0))))


class RingPoseEstimator:
    def __init__(
        self,
        layout: RingLayout,
        cameraMatrix: np.ndarray,
        distortion: np.ndarray | None = None,
        minMarkers: int = MIN_MARKERS,
    ) -> None:
        layout.Validate()
        self.layout = layout
        self.cameraMatrix = np.asarray(cameraMatrix, dtype=np.float64)
        self.distortion = np.zeros(5) if distortion is None else np.asarray(distortion, dtype=np.float64)
        self.minMarkers = minMarkers
        self.knownIds = set(layout.markerIds)
        parameters = cv2.aruco.DetectorParameters()
        parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self.detector = cv2.aruco.ArucoDetector(layout.Dictionary(), parameters)

    def DetectMarkers(self, gray: np.ndarray) -> dict[int, np.ndarray]:
        """Return {markerId: 4x2 image corners} for the ring markers found in a grayscale image."""
        corners, ids, _ = self.detector.detectMarkers(gray)
        if ids is None:
            return {}
        found = {}
        for markerId, markerCorners in zip(ids.ravel().tolist(), corners):
            if markerId in self.knownIds and markerId not in found:
                found[markerId] = markerCorners.reshape(4, 2)
        return found

    def Estimate(self, image: np.ndarray) -> RingPose | None:
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return self.EstimateFromDetections(self.DetectMarkers(gray))

    def EstimateFromDetections(self, detections: dict[int, np.ndarray]) -> RingPose | None:
        """Solve the pose, then drop markers whose corners disagree with it.

        A hand covering part of a marker can still let it decode with displaced
        corners, which biases a plain least-squares solve by several degrees.
        """
        markerIds = sorted(detections)
        while len(markerIds) >= self.minMarkers:
            solution = self._Solve(detections, markerIds)
            if solution is None:
                return None
            rvec, tvec, markerErrors = solution
            worst = int(np.argmax(markerErrors))
            isOutlier = markerErrors[worst] > max(OUTLIER_MIN_PX, OUTLIER_FACTOR * float(np.median(markerErrors)))
            if not isOutlier or len(markerIds) == self.minMarkers:
                rotation, _ = cv2.Rodrigues(rvec)
                rmsError = float(np.sqrt(np.mean(markerErrors**2)))
                pose = RingPose(rotation, tvec.reshape(3), tuple(markerIds), rmsError)
                return pose if self._IsPlausible(pose) else None
            del markerIds[worst]
        return None

    @staticmethod
    def _IsPlausible(pose: RingPose) -> bool:
        """Reject solutions that fit badly as a whole or put the camera under the plate.

        The per-marker outlier filter compares against the median error, so it cannot
        notice a solve where every marker disagrees (a flipped planar solution).
        """
        return pose.reprojectionErrorPx <= MAX_RMS_ERROR_PX and pose.elevationDeg > MIN_ELEVATION_DEG

    def _Solve(self, detections: dict[int, np.ndarray], markerIds: list[int]):
        objectPoints = np.concatenate([self.layout.MarkerCorners(i) for i in markerIds]).astype(np.float64)
        imagePoints = np.concatenate([detections[i] for i in markerIds]).astype(np.float64)
        ok, rvec, tvec = cv2.solvePnP(
            objectPoints, imagePoints, self.cameraMatrix, self.distortion, flags=cv2.SOLVEPNP_SQPNP
        )
        if not ok:
            return None
        rvec, tvec = cv2.solvePnPRefineLM(objectPoints, imagePoints, self.cameraMatrix, self.distortion, rvec, tvec)
        projected, _ = cv2.projectPoints(objectPoints, rvec, tvec, self.cameraMatrix, self.distortion)
        squared = np.sum((projected.reshape(-1, 2) - imagePoints) ** 2, axis=1).reshape(-1, 4)
        return rvec, tvec, np.sqrt(squared.mean(axis=1))
