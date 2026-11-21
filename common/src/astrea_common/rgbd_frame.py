"""RGB-D frame and pinhole intrinsics shared by every capture source (.r3d or USB stream)."""

from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np


@dataclass(frozen=True)
class Intrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    def Matrix(self) -> np.ndarray:
        return np.array([[self.fx, 0.0, self.cx], [0.0, self.fy, self.cy], [0.0, 0.0, 1.0]])

    def ScaledTo(self, width: int, height: int) -> "Intrinsics":
        sx = width / self.width
        sy = height / self.height
        return Intrinsics(self.fx * sx, self.fy * sy, self.cx * sx, self.cy * sy, width, height)

    def MirroredHorizontally(self) -> "Intrinsics":
        return replace(self, cx=self.width - 1 - self.cx)


@dataclass
class RgbdFrame:
    """One capture. `rgb` is HxWx3 uint8 in RGB order, `depthM` is float32 metres (0 = invalid)."""

    index: int
    rgb: np.ndarray
    depthM: np.ndarray
    rgbIntrinsics: Intrinsics
    timestampS: float | None = None
    confidence: np.ndarray | None = None
    poseQuatPos: np.ndarray | None = None

    @property
    def depthIntrinsics(self) -> Intrinsics:
        height, width = self.depthM.shape
        return self.rgbIntrinsics.ScaledTo(width, height)

    def Bgr(self) -> np.ndarray:
        return cv2.cvtColor(self.rgb, cv2.COLOR_RGB2BGR)

    def RgbAtDepthResolution(self) -> np.ndarray:
        height, width = self.depthM.shape
        return cv2.resize(self.rgb, (width, height), interpolation=cv2.INTER_AREA)

    def MirroredHorizontally(self) -> "RgbdFrame":
        return replace(
            self,
            rgb=np.ascontiguousarray(self.rgb[:, ::-1]),
            depthM=np.ascontiguousarray(self.depthM[:, ::-1]),
            confidence=None if self.confidence is None else np.ascontiguousarray(self.confidence[:, ::-1]),
            rgbIntrinsics=self.rgbIntrinsics.MirroredHorizontally(),
        )


def SanitizeDepth(depth: np.ndarray) -> np.ndarray:
    clean = np.asarray(depth, dtype=np.float32).copy()
    clean[~np.isfinite(clean)] = 0.0
    clean[clean < 0.0] = 0.0
    return clean
