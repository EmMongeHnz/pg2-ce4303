"""Temporal average of the depth frames collected while the pose was stable."""

from __future__ import annotations

import numpy as np

MIN_VALID_FRAMES = 2
MAX_SPREAD_M = 0.01


def AverageDepth(depthFrames: list[np.ndarray], maxSpreadM: float = MAX_SPREAD_M) -> np.ndarray:
    """Mean of the valid samples per pixel; pixels seen too few times or too inconsistent become 0."""
    stack = np.stack(depthFrames).astype(np.float32)
    valid = stack > 0
    counts = valid.sum(axis=0)
    mean = np.where(valid, stack, 0.0).sum(axis=0) / np.maximum(counts, 1)
    highest = np.where(valid, stack, -np.inf).max(axis=0)
    lowest = np.where(valid, stack, np.inf).min(axis=0)
    needed = min(MIN_VALID_FRAMES, len(depthFrames))
    consistent = (counts >= needed) & (highest - lowest <= maxSpreadM)
    return np.where(consistent, mean, 0.0).astype(np.float32)
