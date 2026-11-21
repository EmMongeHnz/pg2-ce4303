"""Turns tracked frames into saved keyframes: selection, temporal depth averaging and writing the view package."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

from astrea_common.rgbd_frame import RgbdFrame
from astrea_common.view_package import ViewPackage, WriteView
from capture_service.coverage import CoverageTracker
from capture_service.depth_averager import AverageDepth
from capture_service.keyframe_selector import KeyframeSelector, SelectorParams
from capture_service.ring_pose import RingPose

DEFAULT_AVERAGE_FRAMES = 5
FALLBACK_FPS = 30.0


@dataclass(frozen=True)
class TrackedFrame:
    timestampS: float
    frame: RgbdFrame
    pose: RingPose


def FrameTimestampS(frame: RgbdFrame) -> float:
    return frame.timestampS if frame.timestampS is not None else frame.index / FALLBACK_FPS


class KeyframeRecorder:
    def __init__(self, sessionDir: str | Path, selectorParams: SelectorParams = SelectorParams(), averageFrames: int = DEFAULT_AVERAGE_FRAMES) -> None:
        self.sessionDir = Path(sessionDir)
        self.coverage = CoverageTracker()
        self.viewCount = 0
        self.lastView: ViewPackage | None = None
        self._selector = KeyframeSelector(selectorParams)
        self._recent: deque[TrackedFrame] = deque(maxlen=averageFrames)

    def Feed(self, frame: RgbdFrame, pose: RingPose | None) -> ViewPackage | None:
        """Register one frame; returns the saved view when this frame completes a keyframe."""
        timestampS = FrameTimestampS(frame)
        if pose is not None:
            self._recent.append(TrackedFrame(timestampS, frame, pose))
        windowStartS = self._selector.Update(pose, timestampS)
        if windowStartS is None:
            return None
        return self._Save([tracked for tracked in self._recent if tracked.timestampS >= windowStartS])

    def _Save(self, stableFrames: list[TrackedFrame]) -> ViewPackage:
        best = min(stableFrames, key=lambda tracked: tracked.pose.reprojectionErrorPx)
        view = ViewPackage(
            index=self.viewCount,
            rgb=best.frame.RgbAtDepthResolution(),
            depthM=AverageDepth([tracked.frame.depthM for tracked in stableFrames]),
            intrinsics=best.frame.depthIntrinsics,
            rotation=best.pose.rotation,
            translationMm=best.pose.translationMm,
            azimuthDeg=best.pose.azimuthDeg,
            elevationDeg=best.pose.elevationDeg,
            reprojectionErrorPx=best.pose.reprojectionErrorPx,
        )
        WriteView(self.sessionDir, view)
        self.coverage.Mark(view.azimuthDeg)
        self.viewCount += 1
        self.lastView = view
        return view
