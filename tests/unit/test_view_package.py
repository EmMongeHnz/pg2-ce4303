import numpy as np

from astrea_common.rgbd_frame import Intrinsics
from astrea_common.view_package import ListViewPaths, ReadManifest, ReadView, SessionManifest, ViewPackage, WriteManifest, WriteView

INTRINSICS = Intrinsics(fx=434.1, fy=434.1, cx=240.0, cy=320.0, width=480, height=640)


def MakeView(index: int) -> ViewPackage:
    rng = np.random.default_rng(index)
    return ViewPackage(
        index=index,
        rgb=rng.integers(0, 256, (8, 6, 3), dtype=np.uint8),
        depthM=rng.random((8, 6)).astype(np.float32),
        intrinsics=INTRINSICS,
        rotation=np.eye(3),
        translationMm=np.array([1.0, 2.0, 300.0]),
        azimuthDeg=12.5,
        elevationDeg=35.0,
        reprojectionErrorPx=0.4,
    )


def test_view_round_trip(tmp_path):
    view = MakeView(3)
    path = WriteView(tmp_path, view)
    restored = ReadView(path)
    assert restored.index == 3
    assert np.array_equal(restored.rgb, view.rgb)
    assert np.array_equal(restored.depthM, view.depthM)
    assert restored.intrinsics == INTRINSICS
    assert np.array_equal(restored.translationMm, view.translationMm)
    assert (restored.azimuthDeg, restored.elevationDeg, restored.reprojectionErrorPx) == (12.5, 35.0, 0.4)


def test_views_are_listed_in_index_order(tmp_path):
    for index in (10, 2, 7):
        WriteView(tmp_path, MakeView(index))
    assert [ReadView(path).index for path in ListViewPaths(tmp_path)] == [2, 7, 10]


def test_manifest_round_trip(tmp_path):
    manifest = SessionManifest("cup", {"markerCount": 12}, {"fx": 434.1}, "TrueDepth", False, {"stepDeg": 10.0}, 36)
    WriteManifest(tmp_path / "session", manifest)
    assert ReadManifest(tmp_path / "session") == manifest
