"""Conversion between pipeline objects and the `JOB_ASSIGN` / `JOB_RESULT` messages.

A job is sent as several parts of about `MAX_PART_BYTES` each, so a long transfer never holds the link for
more than a moment and heartbeats interleave. The assemblers collect the parts of one job and return it once
every part arrived. Depth stays float32 and arrays are compressed losslessly: a worker integrates exactly
the same numbers a single node would.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, Sequence, TypeVar

import numpy as np

from astrea_common.fusion import FusionSettings
from astrea_common.messages import (
    ArrayBlob,
    BlockPayload,
    GridPayload,
    IntrinsicsPayload,
    InvalidMessage,
    JobAssign,
    JobResult,
    JobStatus,
    ViewPayload,
)
from astrea_common.rgbd_frame import Intrinsics
from astrea_common.tsdf import GridSpec
from astrea_common.view_package import ViewPackage
from astrea_common.view_preprocess import PreprocessParams
from astrea_common.volume_blocks import VolumeBlock

ZSTD_LEVEL = 3
MAX_PART_BYTES = 1024 * 1024
MAX_PARTS = 4096
PREPROCESS_KEY = "preprocess"

Item = TypeVar("Item")


def ViewToPayload(view: ViewPackage, level: int = ZSTD_LEVEL) -> ViewPayload:
    k = view.intrinsics
    return ViewPayload(
        view.index,
        IntrinsicsPayload(k.fx, k.fy, k.cx, k.cy, k.width, k.height),
        tuple(float(value) for value in np.asarray(view.rotation, dtype=np.float64).reshape(-1)),
        tuple(float(value) for value in np.asarray(view.translationMm, dtype=np.float64).reshape(-1)),
        ArrayBlob.FromNumpy(np.asarray(view.rgb, dtype=np.uint8), level=level),
        ArrayBlob.FromNumpy(np.asarray(view.depthM, dtype=np.float32), level=level),
    )


def ViewFromPayload(payload: ViewPayload) -> ViewPackage:
    k = payload.intrinsics
    rgb, depth = payload.rgb.ToNumpy(), payload.depthM.ToNumpy()
    if rgb.ndim != 3 or rgb.shape[2] != 3 or depth.ndim != 2 or rgb.shape[:2] != depth.shape:
        raise InvalidMessage(f"view {payload.index} has rgb {rgb.shape} and depth {depth.shape}")
    return ViewPackage(
        index=payload.index,
        rgb=np.array(rgb, dtype=np.uint8),
        depthM=np.array(depth, dtype=np.float32),
        intrinsics=Intrinsics(k.fx, k.fy, k.cx, k.cy, k.width, k.height),
        rotation=np.array(payload.rotation, dtype=np.float64).reshape(3, 3),
        translationMm=np.array(payload.translationMm, dtype=np.float64),
        azimuthDeg=0.0,
        elevationDeg=0.0,
        reprojectionErrorPx=0.0,
    )


def GridToPayload(grid: GridSpec) -> GridPayload:
    return GridPayload(tuple(float(value) for value in grid.originMm), float(grid.voxelMm), tuple(int(value) for value in grid.shape))


def GridFromPayload(payload: GridPayload) -> GridSpec:
    return GridSpec(payload.originMm, payload.voxelMm, payload.shape)


def ParamsToPayload(params: PreprocessParams, fusion: FusionSettings) -> dict:
    return {PREPROCESS_KEY: dataclasses.asdict(params), "weights": fusion.weights, "bilinear": fusion.bilinear}


def ParamsFromPayload(payload: dict, truncationMm: float) -> tuple[PreprocessParams, FusionSettings]:
    overrides = payload.get(PREPROCESS_KEY, {})
    if not isinstance(overrides, dict):
        raise InvalidMessage(f"params.{PREPROCESS_KEY} must be a map")
    defaults = PreprocessParams()
    known = {field.name for field in dataclasses.fields(PreprocessParams)}
    unknown = set(overrides) - known
    if unknown:
        raise InvalidMessage(f"unknown preprocess parameter(s): {', '.join(sorted(unknown))}")
    values = {}
    for name, value in overrides.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidMessage(f"preprocess parameter '{name}' must be a number")
        values[name] = type(getattr(defaults, name))(value)
    weights, bilinear = payload.get("weights", True), payload.get("bilinear", True)
    if not isinstance(weights, bool) or not isinstance(bilinear, bool):
        raise InvalidMessage("params.weights and params.bilinear must be booleans")
    return PreprocessParams(**values), FusionSettings(truncationMm, weights, bilinear)


def BlockToPayload(block: VolumeBlock, level: int = ZSTD_LEVEL) -> BlockPayload:
    return BlockPayload(
        block.startIndex,
        block.shape,
        ArrayBlob.FromNumpy(block.tsdf, level=level),
        ArrayBlob.FromNumpy(block.weight, level=level),
        ArrayBlob.FromNumpy(block.color, level=level),
    )


def BlockFromPayload(payload: BlockPayload) -> VolumeBlock:
    tsdf, weight, color = (blob.ToNumpy().astype(np.float32) for blob in (payload.tsdf, payload.weight, payload.color))
    if tsdf.shape != payload.shape or weight.shape != payload.shape or color.shape != (*payload.shape, 3):
        raise InvalidMessage(f"block at {payload.startIndex} has arrays that do not match its shape {payload.shape}")
    return VolumeBlock(payload.startIndex, tsdf, weight, color)


def _ViewBytes(view: ViewPayload) -> int:
    return len(view.rgb.data) + len(view.depthM.data)


def _BlockBytes(block: BlockPayload) -> int:
    return len(block.tsdf.data) + len(block.weight.data) + len(block.color.data)


def _GroupBySize(items: Sequence[Item], sizeOf: Callable[[Item], int], maxBytes: int) -> list[list[Item]]:
    groups: list[list[Item]] = []
    current: list[Item] = []
    currentBytes = 0
    for item in items:
        size = sizeOf(item)
        if current and currentBytes + size > maxBytes:
            groups.append(current)
            current, currentBytes = [], 0
        current.append(item)
        currentBytes += size
    groups.append(current)
    return groups


def BuildAssignParts(
    jobId: str, grid: GridSpec, params: PreprocessParams, fusion: FusionSettings, views: Sequence[ViewPackage], maxPartBytes: int = MAX_PART_BYTES
) -> list[JobAssign]:
    payloads = [ViewToPayload(view) for view in views]
    groups = _GroupBySize(payloads, _ViewBytes, maxPartBytes)
    gridPayload, paramsPayload = GridToPayload(grid), ParamsToPayload(params, fusion)
    return [JobAssign(jobId, index, len(groups), gridPayload, fusion.truncationMm, paramsPayload, tuple(group)) for index, group in enumerate(groups)]


def BuildResultParts(
    jobId: str,
    elapsedS: float,
    viewIndices: Sequence[int],
    keptPercent: Sequence[float],
    blocks: Sequence[BlockPayload],
    maxPartBytes: int = MAX_PART_BYTES,
) -> list[JobResult]:
    groups = _GroupBySize(blocks, _BlockBytes, maxPartBytes)
    parts = []
    for index, group in enumerate(groups):
        first = index == 0
        parts.append(JobResult(jobId, index, len(groups), JobStatus.OK, "", elapsedS, tuple(viewIndices) if first else (), tuple(group), tuple(keptPercent) if first else ()))
    return parts


def BuildErrorResult(jobId: str, message: str, elapsedS: float = 0.0) -> JobResult:
    return JobResult(jobId, 0, 1, JobStatus.ERROR, message, elapsedS, (), ())


@dataclass(frozen=True)
class AssignedJob:
    jobId: str
    grid: GridSpec
    params: PreprocessParams
    fusion: FusionSettings
    views: tuple[ViewPackage, ...]


@dataclass(frozen=True)
class JobOutcome:
    jobId: str
    status: JobStatus
    error: str
    elapsedS: float
    viewIndices: tuple[int, ...]
    keptPercent: tuple[float, ...]
    blocks: tuple[BlockPayload, ...]


class _PartCollector:
    def __init__(self, jobId: str, parts: int) -> None:
        if parts > MAX_PARTS:
            raise InvalidMessage(f"job '{jobId}' announces {parts} parts, above {MAX_PARTS}")
        self.jobId = jobId
        self.count = parts
        self.received: dict[int, object] = {}

    def Add(self, index: int, parts: int, part: object) -> bool:
        if parts != self.count:
            raise InvalidMessage(f"job '{self.jobId}' changed its part count from {self.count} to {parts}")
        if index in self.received:
            raise InvalidMessage(f"part {index} of job '{self.jobId}' arrived twice")
        self.received[index] = part
        return len(self.received) == self.count

    def Ordered(self) -> list:
        return [self.received[index] for index in range(self.count)]


class AssignAssembler:
    """Collects the `JOB_ASSIGN` parts of one job, in any order."""

    def __init__(self) -> None:
        self._collector: _PartCollector | None = None

    def Add(self, part: JobAssign) -> AssignedJob | None:
        if self._collector is None:
            self._collector = _PartCollector(part.jobId, part.parts)
        elif self._collector.jobId != part.jobId:
            raise InvalidMessage(f"job '{part.jobId}' arrived while '{self._collector.jobId}' is incomplete")
        if not self._collector.Add(part.part, part.parts, part):
            return None
        ordered: list[JobAssign] = self._collector.Ordered()
        first = ordered[0]
        for other in ordered[1:]:
            if (other.grid, other.truncationMm, other.params) != (first.grid, first.truncationMm, first.params):
                raise InvalidMessage(f"parts of job '{first.jobId}' disagree on grid or parameters")
        params, fusion = ParamsFromPayload(first.params, first.truncationMm)
        views = tuple(ViewFromPayload(view) for part in ordered for view in part.views)
        return AssignedJob(first.jobId, GridFromPayload(first.grid), params, fusion, views)


class ResultAssembler:
    """Collects the `JOB_RESULT` parts of one job, in any order."""

    def __init__(self) -> None:
        self._collector: _PartCollector | None = None

    def Add(self, part: JobResult) -> JobOutcome | None:
        if self._collector is None:
            self._collector = _PartCollector(part.jobId, part.parts)
        elif self._collector.jobId != part.jobId:
            raise InvalidMessage(f"result of job '{part.jobId}' arrived while '{self._collector.jobId}' is incomplete")
        if not self._collector.Add(part.part, part.parts, part):
            return None
        ordered: list[JobResult] = self._collector.Ordered()
        first = ordered[0]
        blocks = tuple(block for part in ordered for block in part.blocks)
        return JobOutcome(first.jobId, first.status, first.error, first.elapsedS, first.viewIndices, first.keptPercent, blocks)
