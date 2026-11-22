"""Decides when a frame becomes a keyframe: the turntable advanced enough and the pose has settled.

The wait for a stable pose skips frames with a hand on the object or motion blur, and the frames collected while waiting are averaged into the keyframe. Stability is measured
against the pose that opened the current window, so a slow drift also restarts it.
"""

from __future__ import annotations

from dataclasses import dataclass

from capture_service.ring_pose import AngleDifferenceDeg, RingPose

DEFAULT_STEP_DEG = 10.0
DEFAULT_STABLE_SECONDS = 1.2
DEFAULT_MAX_ANGLE_DRIFT_DEG = 0.5
DEFAULT_MAX_DISTANCE_DRIFT_MM = 3.0


@dataclass(frozen=True)
class SelectorParams:
    stepDeg: float = DEFAULT_STEP_DEG
    stableSeconds: float = DEFAULT_STABLE_SECONDS
    maxAngleDriftDeg: float = DEFAULT_MAX_ANGLE_DRIFT_DEG
    maxDistanceDriftMm: float = DEFAULT_MAX_DISTANCE_DRIFT_MM


class KeyframeSelector:
    def __init__(self, params: SelectorParams = SelectorParams()) -> None:
        self.params = params
        self._anchor: RingPose | None = None
        self._windowStartS = 0.0
        self._lastKeyframeAzimuthDeg: float | None = None

    def Update(self, pose: RingPose | None, timestampS: float) -> float | None:
        """Feed one frame's pose. Returns the start time of the stable window when this frame is a keyframe."""
        if pose is None:
            self._anchor = None
            return None
        if self._anchor is None or self._Drifted(pose, self._anchor):
            self._anchor, self._windowStartS = pose, timestampS
            return None
        if timestampS - self._windowStartS < self.params.stableSeconds or not self._HasAdvanced(pose):
            return None
        windowStartS = self._windowStartS
        self._lastKeyframeAzimuthDeg = pose.azimuthDeg
        self._anchor = None
        return windowStartS

    def _Drifted(self, pose: RingPose, anchor: RingPose) -> bool:
        limit = self.params.maxAngleDriftDeg
        return (
            abs(AngleDifferenceDeg(pose.azimuthDeg, anchor.azimuthDeg)) > limit
            or abs(pose.elevationDeg - anchor.elevationDeg) > limit
            or abs(pose.distanceMm - anchor.distanceMm) > self.params.maxDistanceDriftMm
        )

    def _HasAdvanced(self, pose: RingPose) -> bool:
        if self._lastKeyframeAzimuthDeg is None:
            return True
        return abs(AngleDifferenceDeg(pose.azimuthDeg, self._lastKeyframeAzimuthDeg)) >= self.params.stepDeg
