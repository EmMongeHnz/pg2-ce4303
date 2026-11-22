import numpy as np
import pytest

from capture_service.synthetic_depth import MeshDepthRenderer
from sphere_scene import AroundPoses, INTRINSICS, SphereDepthMm, UvSphereMesh


@pytest.fixture(scope="module")
def renderer():
    return MeshDepthRenderer(UvSphereMesh(), INTRINSICS, pointSpacingMm=0.3)


def test_mesh_depth_matches_the_analytic_sphere(renderer):
    rotation, translation = AroundPoses()[5]
    view = renderer.Render(rotation, translation)
    expected = SphereDepthMm(rotation, translation)
    both = (view.depthMm > 0) & (expected > 0)
    assert both.sum() > 1000
    difference = np.abs(view.depthMm[both] - expected[both])
    assert np.median(difference) < 0.05
    assert np.percentile(difference, 95) < 0.5
    silhouetteMismatch = np.mean((view.depthMm > 0) != (expected > 0)) * view.depthMm.size / (expected > 0).sum()
    assert silhouetteMismatch < 0.06


def test_nearest_surface_wins_and_hidden_faces_are_not_reported(renderer):
    rotation, translation = AroundPoses()[0]
    view = renderer.Render(rotation, translation)
    visibleFaces = np.unique(view.faceIds[view.faceIds >= 0])
    normals = renderer.mesh.FaceNormals()[visibleFaces]
    cameraPosition = -rotation.T @ translation
    centroids = renderer.mesh.vertices[renderer.mesh.faces[visibleFaces]].mean(axis=1)
    assert np.mean(np.einsum("ij,ij->i", normals, cameraPosition - centroids) > 0) > 0.99
    assert np.all(view.faceIds[view.depthMm == 0] == -1)


def test_nearer_surface_hides_a_farther_one_in_the_overlap():
    from astrea_common.mesh import TriangleMesh
    from astrea_common.rgbd_frame import Intrinsics

    intrinsics = Intrinsics(fx=100.0, fy=100.0, cx=50.0, cy=50.0, width=100, height=100)

    def Quad(zMm, x0, x1):
        corners = np.array([[x0, -20, zMm], [x1, -20, zMm], [x1, 20, zMm], [x0, 20, zMm]], dtype=float)
        return corners, [[0, 2, 1], [0, 3, 2]]

    farCorners, farFaces = Quad(200.0, -20.0, 20.0)
    nearCorners, nearFaces = Quad(150.0, 0.0, 40.0)
    vertices = np.vstack([farCorners, nearCorners])
    faces = np.array(farFaces + [[i + 4 for i in face] for face in nearFaces])
    renderer = MeshDepthRenderer(TriangleMesh(vertices, faces), intrinsics, pointSpacingMm=0.4)
    view = renderer.Render(np.eye(3), np.zeros(3))
    overlap = view.depthMm[50, 65]
    assert overlap == pytest.approx(150.0, abs=0.01)
    assert view.faceIds[50, 65] >= 2
    assert view.depthMm[50, 40] == pytest.approx(200.0, abs=0.01)
    assert 0 <= view.faceIds[50, 40] < 2
