"""Job messages: views, grids, parameters and blocks survive the trip, and parts reassemble in any order."""

import numpy as np
import pytest

from astrea_common.framing import FrameDecoder
from astrea_common.fusion import FusionSettings
from astrea_common.job_codec import (
    AssignAssembler,
    BlockFromPayload,
    BlockToPayload,
    BuildAssignParts,
    BuildErrorResult,
    BuildResultParts,
    MAX_PARTS,
    ParamsFromPayload,
    ParamsToPayload,
    ResultAssembler,
    ViewFromPayload,
    ViewToPayload,
)
from astrea_common.messages import DecodeMessage, EncodeMessage, InvalidMessage, JobStatus
from astrea_common.tsdf import GridSpec
from astrea_common.view_package import ViewPackage
from astrea_common.view_preprocess import PreprocessParams
from astrea_common.volume_blocks import VolumeBlock
from astrea_common.rgbd_frame import Intrinsics

GRID = GridSpec((-12.5, -7.25, -3.0), 0.65, (30, 22, 18))
INTRINSICS = Intrinsics(512.5, 511.25, 239.5, 319.5, 480, 640)


def MakeView(index: int, seed: int = 0) -> ViewPackage:
    rng = np.random.default_rng(index + seed)
    rotation = np.linalg.qr(rng.normal(size=(3, 3)))[0]
    return ViewPackage(
        index,
        rng.integers(0, 256, (12, 16, 3), dtype=np.uint8),
        rng.uniform(0.0, 0.6, (12, 16)).astype(np.float32),
        Intrinsics(20.5, 21.0, 8.0, 6.0, 16, 12),
        rotation,
        rng.normal(size=3) * 100.0,
        10.0 * index,
        30.0,
        0.1,
    )


def MakeBlock(start=(0, 0, 0), shape=(4, 5, 6), seed=0) -> VolumeBlock:
    rng = np.random.default_rng(seed)
    return VolumeBlock(start, rng.normal(size=shape).astype(np.float32), rng.uniform(0, 3, shape).astype(np.float32), rng.uniform(0, 255, (*shape, 3)).astype(np.float32))


def Send(message):
    frames = FrameDecoder().Feed(EncodeMessage(message))
    return DecodeMessage(frames[0])


def test_view_round_trip_is_bit_exact():
    view = MakeView(3)
    restored = ViewFromPayload(ViewToPayload(view))
    assert restored.index == 3 and restored.intrinsics == view.intrinsics
    np.testing.assert_array_equal(restored.rgb, view.rgb)
    np.testing.assert_array_equal(restored.depthM, view.depthM)
    np.testing.assert_array_equal(restored.rotation, view.rotation)
    np.testing.assert_array_equal(restored.translationMm, view.translationMm)


def test_grid_and_parameters_round_trip():
    params = PreprocessParams(minDepthMm=180.0, minSupportPixels=4, cylinderRadiusMm=95.5)
    fusion = FusionSettings(4.4, weights=False, bilinear=True)
    restored, restoredFusion = ParamsFromPayload(ParamsToPayload(params, fusion), 4.4)
    assert restored == params and restoredFusion == fusion


def test_missing_parameters_fall_back_to_defaults():
    params, fusion = ParamsFromPayload({}, 5.2)
    assert params == PreprocessParams() and fusion == FusionSettings()


@pytest.mark.parametrize("payload", [{"preprocess": {"nonsense": 1.0}}, {"preprocess": {"minDepthMm": "far"}}, {"preprocess": []}, {"weights": "yes"}])
def test_invalid_parameters_are_rejected(payload):
    with pytest.raises(InvalidMessage):
        ParamsFromPayload(payload, 5.2)


def test_assign_parts_reassemble_the_whole_job_in_any_order():
    views = [MakeView(index) for index in range(5)]
    params = PreprocessParams(minDepthMm=150.0)
    parts = BuildAssignParts("job-7", GRID, params, FusionSettings(4.0), views, maxPartBytes=1)
    assert len(parts) == 5
    assembler, job = AssignAssembler(), None
    for part in reversed(parts):
        job = assembler.Add(Send(part))
    assert job is not None and job.jobId == "job-7" and job.grid == GRID and job.params == params
    assert job.fusion.truncationMm == 4.0
    assert [view.index for view in job.views] == [0, 1, 2, 3, 4]
    for view in job.views:
        np.testing.assert_array_equal(view.depthM, views[view.index].depthM)


def test_small_jobs_fit_in_one_part():
    parts = BuildAssignParts("j", GRID, PreprocessParams(), FusionSettings(), [MakeView(0), MakeView(1)])
    assert len(parts) == 1 and len(parts[0].views) == 2


def test_job_without_views_is_still_one_part():
    parts = BuildAssignParts("j", GRID, PreprocessParams(), FusionSettings(), [])
    assert len(parts) == 1 and parts[0].views == ()


def test_assembler_rejects_duplicates_mixed_jobs_and_changed_parameters():
    views = [MakeView(index) for index in range(3)]
    parts = BuildAssignParts("a", GRID, PreprocessParams(), FusionSettings(), views, maxPartBytes=1)
    assembler = AssignAssembler()
    assembler.Add(parts[0])
    with pytest.raises(InvalidMessage):
        assembler.Add(parts[0])
    other = BuildAssignParts("b", GRID, PreprocessParams(), FusionSettings(), views, maxPartBytes=1)
    mixed = AssignAssembler()
    mixed.Add(parts[0])
    with pytest.raises(InvalidMessage):
        mixed.Add(other[1])
    tampered = BuildAssignParts("a", GRID, PreprocessParams(minDepthMm=1.0), FusionSettings(), views, maxPartBytes=1)
    changed = AssignAssembler()
    changed.Add(parts[0])
    changed.Add(parts[1])
    with pytest.raises(InvalidMessage):
        changed.Add(tampered[2])


def test_announcing_too_many_parts_is_rejected():
    part = BuildAssignParts("a", GRID, PreprocessParams(), FusionSettings(), [MakeView(0)])[0]
    huge = type(part)(part.jobId, 0, MAX_PARTS + 1, part.grid, part.truncationMm, part.params, part.views)
    with pytest.raises(InvalidMessage):
        AssignAssembler().Add(huge)


def test_block_round_trip_is_exact():
    block = MakeBlock((8, 4, 2))
    restored = BlockFromPayload(BlockToPayload(block))
    assert restored.startIndex == (8, 4, 2)
    for name in ("tsdf", "weight", "color"):
        np.testing.assert_array_equal(getattr(restored, name), getattr(block, name))


def test_block_with_mismatching_arrays_is_rejected():
    payload = BlockToPayload(MakeBlock(shape=(4, 5, 6)))
    broken = type(payload)(payload.startIndex, (4, 5, 7), payload.tsdf, payload.weight, payload.color)
    with pytest.raises(InvalidMessage):
        BlockFromPayload(broken)


def test_result_parts_carry_blocks_and_metadata_once():
    blocks = [BlockToPayload(MakeBlock((index * 4, 0, 0), seed=index)) for index in range(6)]
    parts = BuildResultParts("job-9", 2.5, [0, 3, 6], [88.0, 91.5, 90.0], blocks, maxPartBytes=1)
    assert len(parts) == 6
    assert parts[0].viewIndices == (0, 3, 6) and parts[1].viewIndices == () and parts[1].keptPercent == ()
    assembler, outcome = ResultAssembler(), None
    for part in reversed(parts):
        outcome = assembler.Add(Send(part))
    assert outcome is not None and outcome.status is JobStatus.OK
    assert outcome.viewIndices == (0, 3, 6) and outcome.keptPercent == (88.0, 91.5, 90.0) and outcome.elapsedS == 2.5
    assert sorted(block.startIndex for block in outcome.blocks) == [(index * 4, 0, 0) for index in range(6)]


def test_result_without_blocks_is_one_part():
    parts = BuildResultParts("j", 0.1, [1], [50.0], [])
    assert len(parts) == 1 and parts[0].blocks == ()


def test_error_result_round_trips():
    outcome = ResultAssembler().Add(Send(BuildErrorResult("j", "grid needs 900 MiB", 0.2)))
    assert outcome.status is JobStatus.ERROR and outcome.error == "grid needs 900 MiB" and outcome.blocks == ()
