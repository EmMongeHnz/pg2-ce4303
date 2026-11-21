"""Refinement of the ArUco turntable poses before fusion.

Two steps, both optional and run on the coordinator before any distribution:

* `FitTurntable` uses the fact that the camera is fixed while the plate turns about one axis: in the plate
  frame the camera centres lie on a circle. Fitting plane and circle removes per-view tilt, distance and
  jitter errors. It cannot change the azimuth of a view: an azimuth error is itself a valid point of the
  circle, so only ICP can correct it.
* `RefineWithIcp` aligns the points of every view to the surface fused from all views (point-to-plane),
  in a few rounds, with the update of each round bounded. It has six degrees of freedom per view, so noise
  in the surface can pull the cameras off their orbit.
* `RefineAzimuthWithIcp` does the same but only turns each view about the fitted turntable axis (one degree
  of freedom), which is all a fixed camera can change. Azimuth is unobservable for an object that is
  rotationally symmetric about the axis, and then it stays as measured.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.spatial import cKDTree

from astrea_common.glb import VertexNormals
from astrea_common.mesh import TriangleMesh
from astrea_common.point_cloud import BackProject
from astrea_common.rotations import MeanRotation, RotationAboutAxis, RotationAngleRad, RotationFromVector
from astrea_common.view_package import ViewPackage, WithPose
from astrea_common.view_preprocess import PreprocessParams, PreprocessView

MIN_TURNTABLE_VIEWS = 6
MIN_TURNTABLE_SPAN_DEG = 90.0
MAX_TURNTABLE_RESIDUAL_MM = 8.0
MIN_ICP_PAIRS = 200
REGULARISATION = 1e-3

Pose = tuple[np.ndarray, np.ndarray]


@dataclass(frozen=True)
class TurntableFit:
    poses: list[Pose] | None
    residualMm: float
    message: str
    axis: np.ndarray | None = None
    origin: np.ndarray | None = None

    @property
    def applied(self) -> bool:
        return self.poses is not None


@dataclass(frozen=True)
class IcpSettings:
    rounds: int = 3
    iterations: int = 10
    maxPairDistanceMm: float = 4.0
    maxRotationDeg: float = 2.0
    maxTranslationMm: float = 3.0
    maxPointsPerView: int = 20_000


def CameraCentre(pose: Pose) -> np.ndarray:
    rotation, translationMm = pose
    return -rotation.T @ translationMm


def OrbitBasis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    reference = np.array([1.0, 0.0, 0.0])
    first = reference - (reference @ axis) * axis
    first /= np.linalg.norm(first)
    return first, np.cross(axis, first)


def FitCircle(planar: np.ndarray) -> tuple[np.ndarray, float]:
    """Algebraic least-squares circle through Nx2 points: returns (centre, radius)."""
    design = np.column_stack([2.0 * planar[:, 0], 2.0 * planar[:, 1], np.ones(len(planar))])
    target = np.sum(planar**2, axis=1)
    cx, cy, offset = np.linalg.lstsq(design, target, rcond=None)[0]
    return np.array([cx, cy]), math.sqrt(max(offset + cx * cx + cy * cy, 0.0))


def AngularSpanDeg(angles: np.ndarray) -> float:
    ordered = np.sort(angles % (2.0 * math.pi))
    gaps = np.diff(np.concatenate([ordered, [ordered[0] + 2.0 * math.pi]]))
    return math.degrees(2.0 * math.pi - gaps.max())


def FitTurntable(poses: list[Pose], maxResidualMm: float = MAX_TURNTABLE_RESIDUAL_MM) -> TurntableFit:
    """Snap the camera centres onto the circle they should follow and give every view the common orientation."""
    if len(poses) < MIN_TURNTABLE_VIEWS:
        return TurntableFit(None, math.nan, f"needs at least {MIN_TURNTABLE_VIEWS} views, got {len(poses)}")
    centres = np.array([CameraCentre(pose) for pose in poses])
    mean = centres.mean(axis=0)
    axis = np.linalg.svd(centres - mean)[2][2]
    if axis[2] < 0:
        axis = -axis
    first, second = OrbitBasis(axis)
    planar = np.column_stack([(centres - mean) @ first, (centres - mean) @ second])
    centre2d, radius = FitCircle(planar)
    angles = np.arctan2(planar[:, 1] - centre2d[1], planar[:, 0] - centre2d[0])
    span = AngularSpanDeg(angles)
    if span < MIN_TURNTABLE_SPAN_DEG:
        return TurntableFit(None, math.nan, f"views span only {span:.0f} deg of the turntable")
    radial = np.hypot(planar[:, 0] - centre2d[0], planar[:, 1] - centre2d[1]) - radius
    outOfPlane = (centres - mean) @ axis
    residual = float(np.sqrt(np.mean(radial**2 + outOfPlane**2)))
    if residual > maxResidualMm:
        return TurntableFit(None, residual, f"camera centres deviate {residual:.1f} mm from a circle (camera moved?)")
    origin = mean + centre2d[0] * first + centre2d[1] * second
    commonOrientation = MeanRotation([RotationAboutAxis(axis, -angle) @ pose[0].T for pose, angle in zip(poses, angles)])
    refined = []
    for angle in angles:
        centre = origin + radius * (math.cos(angle) * first + math.sin(angle) * second)
        rotation = (RotationAboutAxis(axis, angle) @ commonOrientation).T
        refined.append((rotation, -rotation @ centre))
    return TurntableFit(refined, residual, f"circle radius {radius:.1f} mm, residual {residual:.2f} mm", axis, origin)


def SubsamplePoints(points: np.ndarray, maxPoints: int) -> np.ndarray:
    if len(points) <= maxPoints:
        return points
    return points[np.linspace(0, len(points) - 1, maxPoints).astype(np.int64)]


def PointToPlaneStep(points: np.ndarray, vertices: np.ndarray, normals: np.ndarray, tree: cKDTree, maxPairMm: float):
    """Small rotation vector and translation that move `points` onto the surface, or None with too few pairs."""
    distance, nearest = tree.query(points)
    paired = distance < maxPairMm
    if paired.sum() < MIN_ICP_PAIRS:
        return None
    x = points[paired]
    n = normals[nearest[paired]]
    residual = np.einsum("ij,ij->i", x - vertices[nearest[paired]], n)
    jacobian = np.hstack([np.cross(x, n), n])
    normal = jacobian.T @ jacobian
    normal += REGULARISATION * np.trace(normal) / 6.0 * np.eye(6)
    return np.linalg.solve(normal, -jacobian.T @ residual)


def ClampTransform(rotation: np.ndarray, translationMm: np.ndarray, maxRotationDeg: float, maxTranslationMm: float) -> Pose:
    angle = RotationAngleRad(rotation)
    limit = math.radians(maxRotationDeg)
    if angle > limit:
        vector = np.array([rotation[2, 1] - rotation[1, 2], rotation[0, 2] - rotation[2, 0], rotation[1, 0] - rotation[0, 1]])
        rotation = RotationAboutAxis(vector, limit)
    length = float(np.linalg.norm(translationMm))
    if length > maxTranslationMm:
        translationMm = translationMm * (maxTranslationMm / length)
    return rotation, translationMm


def AlignView(cameraPoints: np.ndarray, pose: Pose, surface, settings: IcpSettings) -> Pose:
    """New plate->camera pose that registers the view's points to the surface (all updates in the plate frame)."""
    rotation, translationMm = pose
    vertices, normals, tree = surface
    plate = (cameraPoints - translationMm) @ rotation
    deltaRotation, deltaTranslation = np.eye(3), np.zeros(3)
    for _ in range(settings.iterations):
        moved = plate @ deltaRotation.T + deltaTranslation
        step = PointToPlaneStep(moved, vertices, normals, tree, settings.maxPairDistanceMm)
        if step is None:
            break
        stepRotation = RotationFromVector(step[:3])
        deltaRotation = stepRotation @ deltaRotation
        deltaTranslation = stepRotation @ deltaTranslation + step[3:]
        deltaRotation, deltaTranslation = ClampTransform(deltaRotation, deltaTranslation, settings.maxRotationDeg, settings.maxTranslationMm)
    refinedRotation = rotation @ deltaRotation.T
    return refinedRotation, translationMm - refinedRotation @ deltaTranslation


def AlignViewAboutAxis(cameraPoints: np.ndarray, pose: Pose, orbit: Pose, surface, settings: IcpSettings) -> Pose:
    """Like AlignView but the only motion is a rotation about the turntable axis `orbit = (axis, origin)`."""
    rotation, translationMm = pose
    axis, origin = orbit
    vertices, normals, tree = surface
    plate = (cameraPoints - translationMm) @ rotation
    angle = 0.0
    limit = math.radians(settings.maxRotationDeg)
    for _ in range(settings.iterations):
        deltaRotation = RotationAboutAxis(axis, angle)
        moved = (plate - origin) @ deltaRotation.T + origin
        distance, nearest = tree.query(moved)
        paired = distance < settings.maxPairDistanceMm
        if paired.sum() < MIN_ICP_PAIRS:
            break
        n = normals[nearest[paired]]
        x = moved[paired]
        residual = np.einsum("ij,ij->i", x - vertices[nearest[paired]], n)
        slope = np.einsum("ij,ij->i", np.cross(axis, x - origin), n)
        step = -float(slope @ residual) / (float(slope @ slope) * (1.0 + REGULARISATION) + 1e-12)
        angle = float(np.clip(angle + step, -limit, limit))
    deltaRotation = RotationAboutAxis(axis, angle)
    deltaTranslation = origin - deltaRotation @ origin
    refinedRotation = rotation @ deltaRotation.T
    return refinedRotation, translationMm - refinedRotation @ deltaTranslation


def RunIcpRounds(
    views: list[ViewPackage],
    params: PreprocessParams,
    buildModel: Callable[[list[ViewPackage]], TriangleMesh],
    settings: IcpSettings,
    align: Callable[[int, np.ndarray, Pose, tuple], Pose],
) -> tuple[list[ViewPackage], list[dict]]:
    """Rounds of: fuse all views into a surface, then align each view to it. Returns the views and one summary per round."""
    cameraPoints = [SubsamplePoints(BackProject(PreprocessView(view, params), view.intrinsics)[0], settings.maxPointsPerView) for view in views]
    current = list(views)
    summaries = []
    for roundIndex in range(settings.rounds):
        model = buildModel(current)
        normals = VertexNormals(model.vertices, model.faces)
        surface = (model.vertices, normals, cKDTree(model.vertices))
        updated, rotationChangesDeg, translationChangesMm = [], [], []
        for index, (view, points) in enumerate(zip(current, cameraPoints)):
            rotation, translationMm = align(index, points, (view.rotation, view.translationMm), surface)
            rotationChangesDeg.append(math.degrees(RotationAngleRad(rotation @ view.rotation.T)))
            translationChangesMm.append(float(np.linalg.norm(CameraCentre((rotation, translationMm)) - CameraCentre((view.rotation, view.translationMm)))))
            updated.append(WithPose(view, rotation, translationMm))
        current = updated
        summaries.append({"round": roundIndex + 1, "meanRotationChangeDeg": float(np.mean(rotationChangesDeg)), "meanCameraMoveMm": float(np.mean(translationChangesMm))})
    return current, summaries


def RefineWithIcp(
    views: list[ViewPackage],
    params: PreprocessParams,
    buildModel: Callable[[list[ViewPackage]], TriangleMesh],
    settings: IcpSettings = IcpSettings(),
) -> tuple[list[ViewPackage], list[dict]]:
    return RunIcpRounds(views, params, buildModel, settings, lambda index, points, pose, surface: AlignView(points, pose, surface, settings))


def RefineAzimuthWithIcp(
    views: list[ViewPackage],
    orbits: list[Pose | None],
    params: PreprocessParams,
    buildModel: Callable[[list[ViewPackage]], TriangleMesh],
    settings: IcpSettings = IcpSettings(),
) -> tuple[list[ViewPackage], list[dict]]:
    """`orbits[i]` is (axis, origin) of the turntable fitted for view i's session, or None to leave that view alone."""

    def Align(index: int, points: np.ndarray, pose: Pose, surface) -> Pose:
        return pose if orbits[index] is None else AlignViewAboutAxis(points, pose, orbits[index], surface, settings)

    return RunIcpRounds(views, params, buildModel, settings, Align)
