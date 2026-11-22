import numpy as np
import pytest

from astrea_common.mesh import ReadAsciiPly, SampleSurface, TriangleMesh

PLY_TEXT = """ply
format ascii 1.0
comment test
element vertex 4
property float x
property float y
property float z
property float confidence
element face 2
property list uchar int vertex_indices
end_header
0 0 0 0.9
2 0 0 0.9
0 2 0 0.9
2 2 0 0.9
3 0 1 2
3 1 3 2
"""


def test_ascii_ply_keeps_positions_and_triangles(tmp_path):
    path = tmp_path / "square.ply"
    path.write_text(PLY_TEXT)
    mesh = ReadAsciiPly(path)
    assert mesh.vertices.shape == (4, 3)
    assert mesh.vertices[3].tolist() == [2.0, 2.0, 0.0]
    assert mesh.faces.tolist() == [[0, 1, 2], [1, 3, 2]]


def test_binary_ply_is_rejected(tmp_path):
    path = tmp_path / "binary.ply"
    path.write_text(PLY_TEXT.replace("ascii", "binary_little_endian"))
    with pytest.raises(ValueError, match="ASCII"):
        ReadAsciiPly(path)


def test_samples_lie_on_the_surface_and_follow_the_face_areas():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 5], [4, 0, 5], [0, 4, 5]], dtype=float)
    mesh = TriangleMesh(vertices, np.array([[0, 1, 2], [3, 4, 5]]))
    points, faceOfPoint = SampleSurface(mesh, 20_000, np.random.default_rng(0))
    assert np.allclose(points[faceOfPoint == 1][:, 2], 5.0)
    assert np.all(points[:, 0] + points[:, 1] <= 4.0 + 1e-9)
    share = np.mean(faceOfPoint == 1)
    assert share == pytest.approx(16 / 17, abs=0.01)


def test_sampling_can_be_restricted_to_some_faces():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 5], [4, 0, 5], [0, 4, 5]], dtype=float)
    mesh = TriangleMesh(vertices, np.array([[0, 1, 2], [3, 4, 5]]))
    _, faceOfPoint = SampleSurface(mesh, 100, np.random.default_rng(0), faceIds=np.array([0]))
    assert np.all(faceOfPoint == 0)
