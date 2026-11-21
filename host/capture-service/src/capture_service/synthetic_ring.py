"""Synthetic camera views of the ArUco plate at known poses (ground truth for spike 0b).

The plate is planar, so a view is an exact homography of the top-down plate image.
An upright cylinder stands in for the scanned object and hides the markers behind it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from astrea_common.aruco_ring import PixelToPlateMatrix, PlateDiscMask, RenderPlate, RingLayout

TEXTURE_PX_PER_MM = 8.0
SUPERSAMPLE = 2
BACKGROUND_GRAY = 70
OBJECT_GRAY = 150


@dataclass(frozen=True)
class CameraModel:
    """Pinhole close to the Record3D TrueDepth RGB stream (640x480)."""

    width: int = 640
    height: int = 480
    fx: float = 600.0
    fy: float = 600.0
    cx: float = 319.5
    cy: float = 239.5

    def Matrix(self, scale: float = 1.0) -> np.ndarray:
        return np.array(
            [
                [self.fx * scale, 0.0, (self.cx + 0.5) * scale - 0.5],
                [0.0, self.fy * scale, (self.cy + 0.5) * scale - 0.5],
                [0.0, 0.0, 1.0],
            ]
        )


@dataclass(frozen=True)
class ViewPose:
    azimuthDeg: float
    elevationDeg: float
    distanceMm: float
    targetHeightMm: float = 0.0
    rollDeg: float = 0.0

    def CameraPositionMm(self) -> np.ndarray:
        azimuth = math.radians(self.azimuthDeg)
        elevation = math.radians(self.elevationDeg)
        return self.distanceMm * np.array(
            [math.cos(elevation) * math.cos(azimuth), math.cos(elevation) * math.sin(azimuth), math.sin(elevation)]
        ) + np.array([0.0, 0.0, self.targetHeightMm])

    def PlateToCamera(self) -> tuple[np.ndarray, np.ndarray]:
        """Return (rotation, translationMm) with `xCamera = rotation @ xPlate + translation`."""
        position = self.CameraPositionMm()
        forward = np.array([0.0, 0.0, self.targetHeightMm]) - position
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        down = np.cross(forward, right)
        rotation = np.stack([right, down, forward])
        roll = math.radians(self.rollDeg)
        rollMatrix = np.array(
            [[math.cos(roll), -math.sin(roll), 0.0], [math.sin(roll), math.cos(roll), 0.0], [0.0, 0.0, 1.0]]
        )
        rotation = rollMatrix @ rotation
        return rotation, -rotation @ position


@dataclass(frozen=True)
class SceneOptions:
    objectRadiusMm: float = 45.0
    objectHeightMm: float = 110.0
    handOccluders: int = 0
    noiseSigma: float = 2.0
    blurSigmaPx: float = 0.6
    seed: int = 0
    lightGain: float = 1.0


@dataclass
class SyntheticRingRenderer:
    layout: RingLayout = field(default_factory=RingLayout)
    camera: CameraModel = field(default_factory=CameraModel)

    def __post_init__(self) -> None:
        plate = RenderPlate(self.layout, TEXTURE_PX_PER_MM, printAids=True)
        mask = PlateDiscMask(self.layout, TEXTURE_PX_PER_MM)
        self.texture = np.where(mask > 0, plate, BACKGROUND_GRAY).astype(np.uint8)

    def Render(self, pose: ViewPose, options: SceneOptions = SceneOptions()) -> np.ndarray:
        """Return a grayscale camera image of the plate seen from `pose`."""
        rotation, translation = pose.PlateToCamera()
        size = (self.camera.width * SUPERSAMPLE, self.camera.height * SUPERSAMPLE)
        cameraMatrix = self.camera.Matrix(SUPERSAMPLE)
        planeToImage = cameraMatrix @ np.column_stack([rotation[:, 0], rotation[:, 1], translation])
        homography = planeToImage @ PixelToPlateMatrix(TEXTURE_PX_PER_MM, self.layout.plateRadiusMm)
        image = cv2.warpPerspective(
            self.texture, homography, size, flags=cv2.INTER_LINEAR, borderValue=BACKGROUND_GRAY
        )
        if options.objectRadiusMm > 0:
            self._DrawObject(image, rotation, translation, cameraMatrix, options)
        rng = np.random.default_rng(options.seed)
        for _ in range(options.handOccluders):
            self._DrawHand(image, rng)
        image = cv2.resize(image, (self.camera.width, self.camera.height), interpolation=cv2.INTER_AREA)
        return self._ApplySensor(image, options, rng)

    def _DrawObject(self, image, rotation, translation, cameraMatrix, options: SceneOptions) -> None:
        angles = np.linspace(0.0, 2.0 * math.pi, 72, endpoint=False)
        ring = np.stack([np.cos(angles), np.sin(angles)], axis=1) * options.objectRadiusMm
        bottom = np.column_stack([ring, np.zeros(len(ring))])
        top = np.column_stack([ring, np.full(len(ring), options.objectHeightMm)])
        points = np.concatenate([bottom, top])
        cameraPoints = points @ rotation.T + translation
        if np.any(cameraPoints[:, 2] <= 1.0):
            return
        projected = cameraPoints @ cameraMatrix.T
        pixels = projected[:, :2] / projected[:, 2:]
        hull = cv2.convexHull(np.round(pixels * 16).astype(np.int32))
        cv2.fillConvexPoly(image, hull, OBJECT_GRAY, cv2.LINE_AA, shift=4)

    def _DrawHand(self, image, rng: np.random.Generator) -> None:
        height, width = image.shape
        center = (int(rng.uniform(0.15, 0.85) * width), int(rng.uniform(0.5, 1.0) * height))
        axes = (int(rng.uniform(0.06, 0.12) * width), int(rng.uniform(0.15, 0.3) * height))
        cv2.ellipse(image, center, axes, float(rng.uniform(0, 180)), 0, 360, 185, -1, cv2.LINE_AA)

    def _ApplySensor(self, image: np.ndarray, options: SceneOptions, rng: np.random.Generator) -> np.ndarray:
        signal = image.astype(np.float32) * options.lightGain
        if options.blurSigmaPx > 0:
            signal = cv2.GaussianBlur(signal, (0, 0), options.blurSigmaPx)
        signal += rng.normal(0.0, options.noiseSigma, signal.shape).astype(np.float32)
        return np.clip(signal, 0, 255).astype(np.uint8)


def PlaneDepthMapM(pose: ViewPose, cameraMatrix: np.ndarray, width: int, height: int) -> np.ndarray:
    """Depth (camera z, metres) of the plate plane z=0 seen from `pose`; 0 where the ray misses it."""
    rotation, translation = pose.PlateToCamera()
    v, u = np.indices((height, width), dtype=np.float64)
    rays = np.stack([u, v, np.ones_like(u)], axis=-1) @ np.linalg.inv(cameraMatrix).T
    normalCamera = rotation[:, 2]
    planeOffset = float(normalCamera @ translation)
    denominator = rays @ normalCamera
    with np.errstate(divide="ignore", invalid="ignore"):
        depthMm = planeOffset / denominator
    depthMm[~np.isfinite(depthMm) | (depthMm <= 0)] = 0.0
    return (depthMm / 1000.0).astype(np.float32)
