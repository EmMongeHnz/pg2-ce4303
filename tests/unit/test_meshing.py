import math

import numpy as np
import pytest

pytest.importorskip("skimage")
pytest.importorskip("scipy")
from astrea_common.mesh import TriangleMesh  # noqa: E402
from astrea_common.tsdf import TsdfVolume  # noqa: E402
from coordinator.meshing import ComponentCount, ExtractMesh, LargestComponent  # noqa: E402
from sphere_scene import COLOR, INTRINSICS, SMALL_GRID, AroundPoses, SphereDepthMm  # noqa: E402

RADIUS = 30.0


@pytest.fixture(scope="module")
def fullVolume():
    volume = TsdfVolume(SMALL_GRID)
    rgb = np.full((INTRINSICS.height, INTRINSICS.width, 3), COLOR, dtype=np.uint8)
    for rotation, translation in AroundPoses():
        volume.Integrate(SphereDepthMm(rotation, translation), INTRINSICS, rotation, translation, rgb)
    return volume


def test_sphere_is_reconstructed_as_one_clean_shell(fullVolume):
    mesh = ExtractMesh(fullVolume)
    radial = np.linalg.norm(mesh.vertices, axis=1)
    assert ComponentCount(mesh) == 1
    assert abs(radial.mean() - RADIUS) < 0.25 * SMALL_GRID.voxelMm
    assert np.abs(radial - RADIUS).max() < SMALL_GRID.voxelMm
    assert mesh.FaceAreas().sum() == pytest.approx(4 * math.pi * RADIUS**2, rel=0.03)


def test_mesh_normals_point_outwards(fullVolume):
    mesh = ExtractMesh(fullVolume)
    centroids = mesh.vertices[mesh.faces].mean(axis=1)
    outward = np.einsum("ij,ij->i", mesh.FaceNormals(), centroids)
    assert np.mean(outward > 0) > 0.99


def test_largest_component_drops_the_smaller_island():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [5, 5, 5], [6, 5, 5], [5, 6, 5]], dtype=float)
    faces = np.array([[0, 1, 2], [1, 3, 2], [4, 5, 6]])
    kept = LargestComponent(TriangleMesh(vertices, faces))
    assert len(kept.faces) == 2
    assert len(kept.vertices) == 4
    assert kept.vertices.max() <= 1.0
