import numpy as np
import pytest

pytest.importorskip("skimage")
pytest.importorskip("scipy")
pytest.importorskip("fast_simplification")
pytest.importorskip("zstandard")
from astrea_common.compression import ZstdDecompress  # noqa: E402
from astrea_common.glb import ReadGlbDocument  # noqa: E402
from astrea_common.mesh import TriangleMesh  # noqa: E402
from astrea_common.tsdf import TsdfVolume  # noqa: E402
from coordinator.meshing import ComponentCount  # noqa: E402
from coordinator.postprocess import (  # noqa: E402
    BuildLods,
    Decimate,
    RemoveDegenerate,
    SampleVertexColors,
    TaubinSmooth,
)
from sphere_scene import COLOR, INTRINSICS, SMALL_GRID, SPHERE_RADIUS_MM, AroundPoses, SphereDepthMm, UvSphereMesh  # noqa: E402


@pytest.fixture(scope="module")
def sphereVolume():
    volume = TsdfVolume(SMALL_GRID)
    rgb = np.full((INTRINSICS.height, INTRINSICS.width, 3), COLOR, dtype=np.uint8)
    for rotation, translation in AroundPoses():
        volume.Integrate(SphereDepthMm(rotation, translation), INTRINSICS, rotation, translation, rgb)
    return volume


def test_remove_degenerate_welds_duplicates_and_drops_bad_faces():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0], [2, 2, 2]], dtype=float)
    faces = np.array([[0, 1, 2], [3, 1, 2], [0, 0, 1], [1, 2, 2], [2, 1, 0]])
    clean = RemoveDegenerate(TriangleMesh(vertices, faces))
    assert len(clean.faces) == 1
    assert len(clean.vertices) == 3


def test_remove_degenerate_drops_zero_area_faces():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 1, 0]], dtype=float)
    faces = np.array([[0, 1, 2], [0, 1, 3]])
    assert len(RemoveDegenerate(TriangleMesh(vertices, faces)).faces) == 1


def test_taubin_removes_noise_without_shrinking_the_sphere():
    sphere = UvSphereMesh(rings=30, segments=60)
    rng = np.random.default_rng(1)
    noisy = TriangleMesh(sphere.vertices + rng.normal(0.0, 0.8, sphere.vertices.shape), sphere.faces)
    smooth = TaubinSmooth(noisy)
    noisyError = np.abs(np.linalg.norm(noisy.vertices, axis=1) - SPHERE_RADIUS_MM)
    smoothRadial = np.linalg.norm(smooth.vertices, axis=1)
    assert np.abs(smoothRadial - SPHERE_RADIUS_MM).mean() < 0.4 * noisyError.mean()
    assert smoothRadial.mean() == pytest.approx(SPHERE_RADIUS_MM, rel=0.02)


def test_decimate_reaches_the_budget_and_keeps_the_shape():
    sphere = RemoveDegenerate(UvSphereMesh(rings=60, segments=120))
    reduced = Decimate(sphere, 1000)
    assert 0.95 * 1000 <= len(reduced.faces) <= 1000
    assert ComponentCount(reduced) == 1
    radial = np.linalg.norm(reduced.vertices, axis=1)
    assert np.abs(radial - SPHERE_RADIUS_MM).max() < 0.05 * SPHERE_RADIUS_MM


def test_decimate_leaves_a_small_mesh_alone():
    sphere = UvSphereMesh(rings=10, segments=20)
    assert Decimate(sphere, 10_000) is sphere


def test_vertex_colors_come_from_the_volume(sphereVolume):
    surface = UvSphereMesh(rings=20, segments=40)
    colors = SampleVertexColors(sphereVolume, surface.vertices)
    assert colors.shape == (len(surface.vertices), 3)
    assert np.all(colors == COLOR)


def test_lods_respect_budgets_and_export_to_glb(sphereVolume):
    lods = BuildLods(sphereVolume, highTriangles=1500, lowTriangles=400)
    assert len(lods["low"].mesh.faces) <= 400 < len(lods["high"].mesh.faces) <= 1500
    for lod in lods.values():
        assert ComponentCount(lod.mesh) == 1
        assert lod.colors.shape == (len(lod.mesh.vertices), 3)
        document, _ = ReadGlbDocument(ZstdDecompress(lod.ToCompressedGlb()))
        position = document["accessors"][document["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
        assert position["count"] == len(lod.mesh.vertices)
        assert max(position["max"]) < 0.05


def test_full_lod_is_not_decimated(sphereVolume):
    lods = BuildLods(sphereVolume, highTriangles=1500, lowTriangles=400)
    assert list(lods) == ["full", "high", "low"]
    assert len(lods["full"].mesh.faces) > 1500
    assert ComponentCount(lods["full"].mesh) == 1


def test_taubin_iterations_can_be_disabled(sphereVolume):
    smoothed = BuildLods(sphereVolume, highTriangles=1500, lowTriangles=400)["full"].mesh
    raw = BuildLods(sphereVolume, highTriangles=1500, lowTriangles=400, taubinIterations=0)["full"].mesh
    assert len(raw.faces) == len(smoothed.faces)
    assert not np.allclose(raw.vertices, smoothed.vertices)


def test_vertex_colour_does_not_bleed_from_unobserved_voxels():
    volume = TsdfVolume(SMALL_GRID)
    volume.weight[20:30, 20:30, 20:30] = 1.0
    volume.color[20:30, 20:30, 20:30] = (200.0, 100.0, 50.0)
    edgeVertex = np.array([SMALL_GRID.AxisMm(0)[29] + 0.9 * SMALL_GRID.voxelMm, SMALL_GRID.AxisMm(1)[25], SMALL_GRID.AxisMm(2)[25]])
    assert np.all(SampleVertexColors(volume, edgeVertex[None, :]) == (200, 100, 50))


def test_vertex_colour_interpolates_between_observed_voxels():
    volume = TsdfVolume(SMALL_GRID)
    volume.weight[20:22, 20:22, 20:22] = 1.0
    volume.color[20, 20:22, 20:22] = 0.0
    volume.color[21, 20:22, 20:22] = 100.0
    middle = np.array([SMALL_GRID.AxisMm(0)[20] + 0.5 * SMALL_GRID.voxelMm, SMALL_GRID.AxisMm(1)[20], SMALL_GRID.AxisMm(2)[20]])
    assert SampleVertexColors(volume, middle[None, :])[0, 0] == 50
