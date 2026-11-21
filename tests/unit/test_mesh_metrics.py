import pytest

pytest.importorskip("scipy")
from astrea_common.mesh import TriangleMesh  # noqa: E402
from coordinator.mesh_metrics import AccuracyStats, CompletenessStats  # noqa: E402
from sphere_scene import UvSphereMesh  # noqa: E402

import numpy as np  # noqa: E402


def test_identical_meshes_have_near_zero_distance():
    sphere = UvSphereMesh(rings=60, segments=120)
    stats = AccuracyStats(sphere, sphere, samples=50_000)
    assert stats.meanMm < 0.15
    assert stats.maxMm < 0.6


def test_accuracy_reports_the_radial_offset():
    reference = UvSphereMesh(radius=30.0, rings=60, segments=120)
    larger = UvSphereMesh(radius=31.0, rings=60, segments=120)
    stats = AccuracyStats(larger, reference, samples=50_000)
    assert stats.meanMm == pytest.approx(1.0, abs=0.1)
    assert stats.p95Mm < 1.3


def test_completeness_only_counts_the_visible_faces():
    reference = UvSphereMesh(rings=60, segments=120)
    centroids = reference.vertices[reference.faces].mean(axis=1)
    upperFaces = np.flatnonzero(centroids[:, 2] > 0)
    upperHalf = TriangleMesh(reference.vertices, reference.faces[upperFaces])
    onUpperOnly = CompletenessStats(upperHalf, reference, upperFaces, samples=50_000)
    onEverything = CompletenessStats(upperHalf, reference, np.arange(len(reference.faces)), samples=50_000)
    assert onUpperOnly.meanMm < 0.15
    assert onEverything.meanMm > 3.0
