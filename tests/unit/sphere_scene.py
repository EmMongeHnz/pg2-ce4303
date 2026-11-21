"""Analytic sphere scene shared by the TSDF, meshing and renderer tests."""

import math

import numpy as np

from astrea_common.mesh import TriangleMesh
from astrea_common.rgbd_frame import Intrinsics
from astrea_common.tsdf import GridSpec
from capture_service.synthetic_ring import ViewPose

SPHERE_RADIUS_MM = 30.0
CAMERA_DISTANCE_MM = 200.0
INTRINSICS = Intrinsics(fx=200.0, fy=200.0, cx=80.0, cy=60.0, width=160, height=120)
SMALL_GRID = GridSpec(originMm=(-63.0, -63.0, -63.0), voxelMm=2.0, shape=(64, 64, 64))
COLOR = (200, 100, 50)


def AroundPoses(azimuthStepDeg=30, elevationsDeg=(-45, 0, 45)):
    poses = [ViewPose(az, el, CAMERA_DISTANCE_MM) for el in elevationsDeg for az in range(0, 360, azimuthStepDeg)]
    return [pose.PlateToCamera() for pose in poses]


def SphereDepthMm(rotation, translationMm, intrinsics=INTRINSICS, radius=SPHERE_RADIUS_MM):
    """Exact z-depth of a sphere centred on the plate origin, 0 where the ray misses it."""
    v, u = np.indices((intrinsics.height, intrinsics.width))
    rays = np.stack([(u - intrinsics.cx) / intrinsics.fx, (v - intrinsics.cy) / intrinsics.fy, np.ones(u.shape)], axis=-1)
    center = translationMm
    rayDotCenter = rays @ center
    raySquared = np.sum(rays**2, axis=-1)
    discriminant = rayDotCenter**2 - raySquared * (center @ center - radius**2)
    hit = discriminant >= 0
    depth = (rayDotCenter - np.sqrt(np.where(hit, discriminant, 0.0))) / raySquared
    return np.where(hit, depth, 0.0).astype(np.float32)


def UvSphereMesh(radius=SPHERE_RADIUS_MM, rings=120, segments=240) -> TriangleMesh:
    theta = np.linspace(0.0, math.pi, rings + 1)[:, None]
    phi = np.linspace(0.0, 2 * math.pi, segments, endpoint=False)[None, :]
    vertices = np.stack([radius * np.sin(theta) * np.cos(phi), radius * np.sin(theta) * np.sin(phi), radius * np.cos(theta) + 0 * phi], axis=-1).reshape(-1, 3)
    index = np.arange((rings + 1) * segments).reshape(rings + 1, segments)
    nextSegment = np.roll(index, -1, axis=1)
    a, b, c, d = index[:-1], nextSegment[:-1], index[1:], nextSegment[1:]
    faces = np.concatenate([np.stack([a, c, b], axis=-1).reshape(-1, 3), np.stack([b, c, d], axis=-1).reshape(-1, 3)])
    return TriangleMesh(vertices, faces)
