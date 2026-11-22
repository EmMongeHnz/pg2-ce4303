"""Surface extraction from a TSDF volume: marching cubes restricted to observed voxels, largest component."""

from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from skimage.measure import marching_cubes

from astrea_common.mesh import TriangleMesh
from astrea_common.tsdf import TsdfVolume


def ExtractMesh(volume: TsdfVolume) -> TriangleMesh:
    """Zero level set of the TSDF in plate-frame millimetres, only where all eight voxels of a cube were observed.

    Unobserved voxels keep the value +1 next to observed interior voxels near -1, so marching cubes would
    invent a shell there. The `mask` argument of scikit-image only looks at one corner of each cube, so
    the faces are filtered afterwards.
    """
    grid = volume.grid
    observed = volume.weight > 0
    vertices, faces, _, _ = marching_cubes(volume.tsdf, level=0.0, spacing=(grid.voxelMm,) * 3, mask=observed)
    vertices = vertices + np.array(grid.originMm)
    cubeIndex = np.floor((vertices[faces].mean(axis=1) - np.array(grid.originMm)) / grid.voxelMm).astype(np.int64)
    cubeIndex = np.clip(cubeIndex, 0, np.array(grid.shape) - 2)
    keep = FullyObservedCubes(observed)[cubeIndex[:, 0], cubeIndex[:, 1], cubeIndex[:, 2]]
    return CompactMesh(TriangleMesh(vertices, faces.astype(np.int64)[keep]))


def FullyObservedCubes(observed: np.ndarray) -> np.ndarray:
    """Boolean array of shape (nx-1, ny-1, nz-1): True where the cube's eight corner voxels are all observed."""
    nx, ny, nz = observed.shape
    cubes = np.ones((nx - 1, ny - 1, nz - 1), dtype=bool)
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                cubes &= observed[dx : nx - 1 + dx, dy : ny - 1 + dy, dz : nz - 1 + dz]
    return cubes


def CompactMesh(mesh: TriangleMesh) -> TriangleMesh:
    used, inverse = np.unique(mesh.faces, return_inverse=True)
    return TriangleMesh(mesh.vertices[used], inverse.reshape(-1, 3))


def LargestComponent(mesh: TriangleMesh) -> TriangleMesh:
    """Keep the connected component with the most faces and drop vertices that are no longer used."""
    faces = mesh.faces
    count = len(mesh.vertices)
    rows = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
    cols = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
    graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(count, count))
    _, labels = connected_components(graph, directed=False)
    faceLabels = labels[faces[:, 0]]
    largest = np.bincount(faceLabels).argmax()
    return CompactMesh(TriangleMesh(mesh.vertices, faces[faceLabels == largest]))


def ComponentCount(mesh: TriangleMesh) -> int:
    faces = mesh.faces
    count = len(mesh.vertices)
    rows = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
    cols = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
    graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(count, count))
    return int(connected_components(graph, directed=False)[0])
