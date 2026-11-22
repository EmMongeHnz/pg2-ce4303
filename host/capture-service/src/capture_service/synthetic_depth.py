"""Depth maps of a known triangle mesh seen from known poses, for the synthetic reconstruction test (M3).

The mesh surface is sampled densely once and every view z-buffers those points to find the visible face of
each pixel, which is much faster than rasterising triangles that are about a pixel in size. The depth is then
the exact intersection of the pixel-centre ray with that face's plane: taking the depth of the nearest
sample instead would bias tilted surfaces towards the camera by up to half a pixel footprint times tan(tilt).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from astrea_common.mesh import SampleSurface, TriangleMesh
from astrea_common.rgbd_frame import Intrinsics

POINT_SPACING_MM = 0.2
MIN_RAY_NORMAL_COSINE = 1e-3
MAX_PLANE_CORRECTION_MM = 2.0
SEED = 1


@dataclass(frozen=True)
class DepthView:
    depthMm: np.ndarray
    faceIds: np.ndarray


class MeshDepthRenderer:
    def __init__(self, mesh: TriangleMesh, intrinsics: Intrinsics, pointSpacingMm: float = POINT_SPACING_MM) -> None:
        self.mesh = mesh
        self.intrinsics = intrinsics
        count = int(mesh.FaceAreas().sum() / pointSpacingMm**2)
        self.points, self.faceOfPoint = SampleSurface(mesh, count, np.random.default_rng(SEED))
        self.pointNormals = mesh.FaceNormals()[self.faceOfPoint]

    def Render(self, rotation: np.ndarray, translationMm: np.ndarray) -> DepthView:
        """`xCamera = rotation @ xPlate + translationMm`. Pixels without surface have depth 0 and face id -1."""
        cameraPosition = -rotation.T @ translationMm
        facing = np.einsum("ij,ij->i", self.pointNormals, cameraPosition - self.points) > 0
        cameraPoints = self.points[facing] @ rotation.T + translationMm
        cameraNormals = self.pointNormals[facing] @ rotation.T
        faceIds = self.faceOfPoint[facing]
        depth = cameraPoints[:, 2]
        k = self.intrinsics
        col = np.rint(k.fx * cameraPoints[:, 0] / depth + k.cx).astype(np.int64)
        row = np.rint(k.fy * cameraPoints[:, 1] / depth + k.cy).astype(np.int64)
        inside = np.flatnonzero((depth > 0) & (col >= 0) & (col < k.width) & (row >= 0) & (row < k.height))
        pixelOfSample = row[inside] * k.width + col[inside]
        byPixelThenDepth = np.lexsort((depth[inside], pixelOfSample))
        covered, firstOfPixel = np.unique(pixelOfSample[byPixelThenDepth], return_index=True)
        sample = inside[byPixelThenDepth][firstOfPixel]
        rays = np.stack([(covered % k.width - k.cx) / k.fx, (covered // k.width - k.cy) / k.fy, np.ones(len(covered))], axis=1)
        alongRay = np.einsum("ij,ij->i", cameraNormals[sample], rays)
        planeDepth = np.einsum("ij,ij->i", cameraNormals[sample], cameraPoints[sample]) / np.where(np.abs(alongRay) > MIN_RAY_NORMAL_COSINE, alongRay, np.inf)
        usable = np.abs(planeDepth - depth[sample]) < MAX_PLANE_CORRECTION_MM
        depthBuffer = np.zeros(k.width * k.height, dtype=np.float32)
        faceBuffer = np.full(k.width * k.height, -1, dtype=np.int64)
        depthBuffer[covered] = np.where(usable, planeDepth, depth[sample])
        faceBuffer[covered] = faceIds[sample]
        return DepthView(depthBuffer.reshape(k.height, k.width), faceBuffer.reshape(k.height, k.width))
