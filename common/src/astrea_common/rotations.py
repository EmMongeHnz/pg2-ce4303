"""Small rotation helpers shared by pose averaging and pose refinement."""

from __future__ import annotations

import math

import numpy as np


def RotationAboutAxis(axis: np.ndarray, angleRad: float) -> np.ndarray:
    """Rodrigues rotation matrix; `axis` need not be normalised."""
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    skew = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]])
    return np.eye(3) + math.sin(angleRad) * skew + (1.0 - math.cos(angleRad)) * skew @ skew


def RotationFromVector(vector: np.ndarray) -> np.ndarray:
    """Rotation by |vector| radians about vector."""
    angle = float(np.linalg.norm(vector))
    return np.eye(3) if angle < 1e-12 else RotationAboutAxis(vector, angle)


def RotationAngleRad(rotation: np.ndarray) -> float:
    return math.acos(float(np.clip((np.trace(rotation) - 1.0) / 2.0, -1.0, 1.0)))


def MeanRotation(rotations: list[np.ndarray]) -> np.ndarray:
    """Chordal L2 mean: the rotation closest to the arithmetic mean of the matrices."""
    left, _, right = np.linalg.svd(np.sum(rotations, axis=0))
    correction = np.diag([1.0, 1.0, np.linalg.det(left @ right)])
    return left @ correction @ right
