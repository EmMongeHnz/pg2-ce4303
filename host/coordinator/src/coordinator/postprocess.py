"""Mesh clean-up after marching cubes: degenerate removal, Taubin smoothing, quadric decimation, vertex colour, GLB export."""

from __future__ import annotations

from dataclasses import dataclass

import fast_simplification
import numpy as np
from scipy.sparse import coo_matrix, csr_matrix, diags

from astrea_common.compression import ZstdCompress
from astrea_common.glb import EncodeGlb
from astrea_common.mesh import TriangleMesh
from astrea_common.tsdf import TsdfVolume
from coordinator.meshing import CompactMesh, ExtractMesh, LargestComponent

LOD_HIGH_TRIANGLES = 30_000
LOD_LOW_TRIANGLES = 8_000
WELD_DECIMALS = 4
MIN_FACE_AREA_MM2 = 1e-6
TAUBIN_ITERATIONS = 10
TAUBIN_LAMBDA = 0.5
TAUBIN_MU = -0.53
DECIMATION_AGGRESSIVENESS = 5.0
MM_PER_METRE = 1000.0


@dataclass(frozen=True)
class Lod:
    name: str
    mesh: TriangleMesh
    colors: np.ndarray

    def ToGlb(self) -> bytes:
        return EncodeGlb(self.mesh.vertices / MM_PER_METRE, self.mesh.faces, self.colors)

    def ToCompressedGlb(self) -> bytes:
        return ZstdCompress(self.ToGlb())


def RemoveDegenerate(mesh: TriangleMesh) -> TriangleMesh:
    """Weld coincident vertices, then drop collapsed, zero-area and duplicated faces."""
    rounded = np.round(mesh.vertices, WELD_DECIMALS)
    _, firstIndex, inverse = np.unique(rounded, axis=0, return_index=True, return_inverse=True)
    welded = TriangleMesh(mesh.vertices[firstIndex], inverse.reshape(-1)[mesh.faces])
    faces = welded.faces
    distinct = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    faces = faces[distinct]
    faces = faces[TriangleMesh(welded.vertices, faces).FaceAreas() > MIN_FACE_AREA_MM2]
    _, uniqueRows = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    return CompactMesh(TriangleMesh(welded.vertices, faces[np.sort(uniqueRows)]))


def NeighbourAverage(mesh: TriangleMesh) -> csr_matrix:
    """Row-normalised vertex adjacency matrix, so `matrix @ vertices` is the mean of each vertex's neighbours."""
    faces = mesh.faces
    count = len(mesh.vertices)
    edgeStarts = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
    edgeEnds = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
    rows = np.concatenate([edgeStarts, edgeEnds])
    cols = np.concatenate([edgeEnds, edgeStarts])
    adjacency = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(count, count)).tocsr()
    adjacency.data[:] = 1.0
    degree = np.asarray(adjacency.sum(axis=1)).reshape(-1)
    return diags(1.0 / np.maximum(degree, 1.0)) @ adjacency


def TaubinSmooth(
    mesh: TriangleMesh,
    iterations: int = TAUBIN_ITERATIONS,
    shrinkFactor: float = TAUBIN_LAMBDA,
    inflateFactor: float = TAUBIN_MU,
) -> TriangleMesh:
    """Alternating shrink/inflate Laplacian steps: they remove voxel staircase noise with little volume loss."""
    average = NeighbourAverage(mesh)
    vertices = mesh.vertices.copy()
    for _ in range(iterations):
        for factor in (shrinkFactor, inflateFactor):
            vertices += factor * (average @ vertices - vertices)
    return TriangleMesh(vertices, mesh.faces)


def Decimate(mesh: TriangleMesh, targetFaces: int) -> TriangleMesh:
    """Quadric decimation; collapses can pinch off a loose triangle, so only the largest component is kept."""
    if len(mesh.faces) <= targetFaces:
        return mesh
    vertices, faces = fast_simplification.simplify(
        mesh.vertices, mesh.faces.astype(np.int32), target_count=targetFaces, agg=DECIMATION_AGGRESSIVENESS
    )
    return LargestComponent(RemoveDegenerate(TriangleMesh(vertices, faces.astype(np.int64))))


def SampleVertexColors(volume: TsdfVolume, vertices: np.ndarray) -> np.ndarray:
    """Colour of the voxel that contains each vertex (nearest voxel centre)."""
    grid = volume.grid
    index = np.rint((vertices - np.array(grid.originMm)) / grid.voxelMm).astype(np.int64)
    index = np.clip(index, 0, np.array(grid.shape) - 1)
    return np.clip(np.rint(volume.color[index[:, 0], index[:, 1], index[:, 2]]), 0, 255).astype(np.uint8)


def CleanMesh(volume: TsdfVolume) -> TriangleMesh:
    mesh = RemoveDegenerate(ExtractMesh(volume))
    return TaubinSmooth(LargestComponent(mesh))


def BuildLods(volume: TsdfVolume, highTriangles: int = LOD_HIGH_TRIANGLES, lowTriangles: int = LOD_LOW_TRIANGLES) -> dict[str, Lod]:
    """LOD-high is decimated from the cleaned mesh and LOD-low from LOD-high, so both share the same silhouette."""
    high = Decimate(CleanMesh(volume), highTriangles)
    low = Decimate(high, lowTriangles)
    return {
        name: Lod(name, mesh, SampleVertexColors(volume, mesh.vertices))
        for name, mesh in (("high", high), ("low", low))
    }
