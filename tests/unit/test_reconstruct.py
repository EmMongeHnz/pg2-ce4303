import json

import numpy as np

from astrea_common.view_package import WriteView
from coordinator.reconstruct import Reconstruct
from sphere_scene import SMALL_GRID
from test_distributed_equivalence import Views


def WriteSession(sessionDir, views) -> None:
    for view in views:
        WriteView(sessionDir, view)


def test_session_is_reconstructed_into_one_component_per_lod(tmp_path):
    WriteSession(tmp_path / "scan", Views())
    report = Reconstruct([tmp_path / "scan"], tmp_path / "model", grid=SMALL_GRID)
    assert report.passesGate
    assert report.viewCount == 24
    assert all(lod.components == 1 for lod in report.lods.values())
    assert (tmp_path / "model" / "lod-high.glb").stat().st_size > 0
    assert (tmp_path / "model" / "lod-low.glb").stat().st_size > 0
    assert json.loads((tmp_path / "model" / "report.json").read_text())["viewCount"] == 24


def test_plate_is_not_part_of_the_reconstructed_model(tmp_path):
    WriteSession(tmp_path / "scan", Views())
    Reconstruct([tmp_path / "scan"], tmp_path / "model", grid=SMALL_GRID)
    data = (tmp_path / "model" / "lod-high.glb").read_bytes()
    assert len(data) < 200_000


def test_two_sessions_equal_one_session_with_all_the_views(tmp_path):
    views = Views()
    WriteSession(tmp_path / "all", views)
    WriteSession(tmp_path / "first", views[:12])
    WriteSession(tmp_path / "second", views[12:])
    single = Reconstruct([tmp_path / "all"], tmp_path / "model-all", grid=SMALL_GRID)
    split = Reconstruct([tmp_path / "first", tmp_path / "second"], tmp_path / "model-split", grid=SMALL_GRID)
    assert single.lods["high"].faces == split.lods["high"].faces
    assert (tmp_path / "model-all" / "lod-high.glb").read_bytes() == (tmp_path / "model-split" / "lod-high.glb").read_bytes()
