"""Geometry of the ArUco ring printed on the turntable plate.

The plate frame has its origin at the plate center, +z pointing up out of the
plate, +x towards marker 0 and +y towards the 90° position. Units are mm.
Each marker lies flat on the plate with its top edge pointing radially outward,
so the generator and the pose estimator share exactly the same corner model.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

DEFAULT_DICTIONARY = "DICT_4X4_50"


@dataclass(frozen=True)
class RingLayout:
    plateDiameterMm: float = 180.0
    markerCount: int = 12
    markerSizeMm: float = 24.0
    markerCenterRadiusMm: float = 70.0
    firstMarkerId: int = 0
    dictionaryName: str = DEFAULT_DICTIONARY

    @property
    def plateRadiusMm(self) -> float:
        return self.plateDiameterMm / 2.0

    @property
    def markerIds(self) -> list[int]:
        return [self.firstMarkerId + i for i in range(self.markerCount)]

    def MarkerAngleDeg(self, markerId: int) -> float:
        return (markerId - self.firstMarkerId) * 360.0 / self.markerCount

    def Dictionary(self) -> cv2.aruco.Dictionary:
        return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, self.dictionaryName))

    def MarkerCorners(self, markerId: int) -> np.ndarray:
        """Return the 4x3 corners of a marker in ArUco order (TL, TR, BR, BL), plate frame."""
        theta = math.radians(self.MarkerAngleDeg(markerId))
        radial = np.array([math.cos(theta), math.sin(theta), 0.0])
        right = np.array([math.sin(theta), -math.cos(theta), 0.0])
        center = radial * self.markerCenterRadiusMm
        half = self.markerSizeMm / 2.0
        return np.array(
            [
                center + half * radial - half * right,
                center + half * radial + half * right,
                center - half * radial + half * right,
                center - half * radial - half * right,
            ]
        )

    def Validate(self) -> None:
        if self.markerCount < 3:
            raise ValueError("the ring needs at least 3 markers")
        half = self.markerSizeMm / 2.0
        outerCornerRadius = math.hypot(self.markerCenterRadiusMm + half, half)
        if outerCornerRadius >= self.plateRadiusMm:
            raise ValueError("markers do not fit inside the plate")
        chord = 2.0 * (self.markerCenterRadiusMm - half) * math.sin(math.pi / self.markerCount)
        if chord <= self.markerSizeMm:
            raise ValueError("neighbouring markers overlap")
        dictionarySize = self.Dictionary().bytesList.shape[0]
        if self.firstMarkerId + self.markerCount > dictionarySize:
            raise ValueError("marker ids exceed the dictionary size")

    def ToJson(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @staticmethod
    def FromJson(text: str) -> "RingLayout":
        return RingLayout(**json.loads(text))

    @staticmethod
    def Load(path: str | Path | None) -> "RingLayout":
        if path is None:
            return RingLayout()
        return RingLayout.FromJson(Path(path).read_text())


TICK_STEP_DEG = 10
TICK_OUTER_GAP_MM = 0.8
ZERO_TICK_MM = 4.0
MAJOR_TICK_MM = 3.0
MINOR_TICK_MM = 2.0


def _TickLengthMm(angleDeg: int) -> float:
    if angleDeg == 0:
        return ZERO_TICK_MM
    return MAJOR_TICK_MM if angleDeg % 30 == 0 else MINOR_TICK_MM


def PlateMmToPixel(points: np.ndarray, pxPerMm: float, plateRadiusMm: float) -> np.ndarray:
    """Map plate-frame (x, y) in mm to pixel-centre coordinates of a top-down plate image."""
    points = np.asarray(points, dtype=np.float64)
    u = (points[..., 0] + plateRadiusMm) * pxPerMm - 0.5
    v = (plateRadiusMm - points[..., 1]) * pxPerMm - 0.5
    return np.stack([u, v], axis=-1)


def PixelToPlateMatrix(pxPerMm: float, plateRadiusMm: float) -> np.ndarray:
    """3x3 homogeneous transform from plate-image pixels to plate-frame (x, y) in mm."""
    return np.array(
        [
            [1.0 / pxPerMm, 0.0, 0.5 / pxPerMm - plateRadiusMm],
            [0.0, -1.0 / pxPerMm, plateRadiusMm - 0.5 / pxPerMm],
            [0.0, 0.0, 1.0],
        ]
    )


def _DrawMarker(canvas: np.ndarray, layout: RingLayout, markerId: int, pxPerMm: float) -> None:
    dictionary = layout.Dictionary()
    cells = dictionary.markerSize + 2
    marker = cv2.aruco.generateImageMarker(dictionary, markerId, cells)
    side = float(cells)
    source = np.float32([[-0.5, -0.5], [side - 0.5, -0.5], [-0.5, side - 0.5]])
    corners = PlateMmToPixel(layout.MarkerCorners(markerId)[:, :2], pxPerMm, layout.plateRadiusMm)
    destination = np.float32([corners[0], corners[1], corners[3]])
    transform = cv2.getAffineTransform(source, destination)
    size = (canvas.shape[1], canvas.shape[0])
    warped = cv2.warpAffine(marker, transform, size, flags=cv2.INTER_NEAREST, borderValue=255)
    mask = cv2.warpAffine(np.full_like(marker, 255), transform, size, flags=cv2.INTER_NEAREST, borderValue=0)
    canvas[mask > 0] = warped[mask > 0]


def _DrawPrintAids(canvas: np.ndarray, layout: RingLayout, pxPerMm: float) -> None:
    radius = layout.plateRadiusMm
    center = PlateMmToPixel(np.zeros(2), pxPerMm, radius)
    thickness = max(1, round(0.35 * pxPerMm))
    centerPx = (round(center[0]), round(center[1]))
    cv2.circle(canvas, centerPx, round((radius - 0.2) * pxPerMm), 0, thickness, cv2.LINE_AA)
    for angle in range(0, 360, TICK_STEP_DEG):
        length = _TickLengthMm(angle)
        direction = np.array([math.cos(math.radians(angle)), math.sin(math.radians(angle))])
        outer = PlateMmToPixel(direction * (radius - TICK_OUTER_GAP_MM), pxPerMm, radius)
        inner = PlateMmToPixel(direction * (radius - TICK_OUTER_GAP_MM - length), pxPerMm, radius)
        cv2.line(canvas, tuple(np.round(outer).astype(int)), tuple(np.round(inner).astype(int)), 0, thickness, cv2.LINE_AA)
    crossHalf = round(5.0 * pxPerMm)
    cv2.line(canvas, (centerPx[0] - crossHalf, centerPx[1]), (centerPx[0] + crossHalf, centerPx[1]), 0, thickness)
    cv2.line(canvas, (centerPx[0], centerPx[1] - crossHalf), (centerPx[0], centerPx[1] + crossHalf), 0, thickness)


def RenderPlate(layout: RingLayout, pxPerMm: float, printAids: bool = True) -> np.ndarray:
    """Render the plate seen from above as a square grayscale image (white plate, black markers)."""
    layout.Validate()
    side = round(layout.plateDiameterMm * pxPerMm)
    canvas = np.full((side, side), 255, np.uint8)
    for markerId in layout.markerIds:
        _DrawMarker(canvas, layout, markerId, pxPerMm)
    if printAids:
        _DrawPrintAids(canvas, layout, pxPerMm)
    return canvas


def PlateDiscMask(layout: RingLayout, pxPerMm: float) -> np.ndarray:
    side = round(layout.plateDiameterMm * pxPerMm)
    mask = np.zeros((side, side), np.uint8)
    cv2.circle(mask, (side // 2, side // 2), side // 2, 255, -1)
    return mask
