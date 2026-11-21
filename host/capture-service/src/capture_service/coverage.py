"""Azimuth coverage of a scan: which 10° sectors of the turntable already have a keyframe."""

from __future__ import annotations

import math

import cv2
import numpy as np

SECTOR_DEG = 10.0
SECTOR_COUNT = int(360.0 / SECTOR_DEG)
COVERED_COLOR = (0, 200, 0)
MISSING_COLOR = (70, 70, 70)
CURRENT_COLOR = (0, 200, 255)
RING_THICKNESS_PX = 10


def SectorOf(azimuthDeg: float) -> int:
    return int((azimuthDeg % 360.0) // SECTOR_DEG)


class CoverageTracker:
    def __init__(self) -> None:
        self.covered = np.zeros(SECTOR_COUNT, dtype=bool)

    def Mark(self, azimuthDeg: float) -> None:
        self.covered[SectorOf(azimuthDeg)] = True

    @property
    def coveredCount(self) -> int:
        return int(self.covered.sum())


def DrawCoverageRing(image: np.ndarray, tracker: CoverageTracker, currentAzimuthDeg: float | None, center: tuple[int, int], radiusPx: int) -> None:
    """Draw one arc per sector (azimuth 0 to the right, counterclockwise as seen from above)."""
    for sector in range(SECTOR_COUNT):
        color = COVERED_COLOR if tracker.covered[sector] else MISSING_COLOR
        _DrawSector(image, sector, color, center, radiusPx)
    if currentAzimuthDeg is not None:
        angle = math.radians(currentAzimuthDeg)
        tip = (round(center[0] + (radiusPx + 8) * math.cos(angle)), round(center[1] - (radiusPx + 8) * math.sin(angle)))
        cv2.line(image, center, tip, CURRENT_COLOR, 2, cv2.LINE_AA)


def _DrawSector(image: np.ndarray, sector: int, color: tuple[int, int, int], center: tuple[int, int], radiusPx: int) -> None:
    start, end = sector * SECTOR_DEG, (sector + 1) * SECTOR_DEG
    cv2.ellipse(image, center, (radiusPx, radiusPx), 0, -end, -start, color, RING_THICKNESS_PX, cv2.LINE_AA)
