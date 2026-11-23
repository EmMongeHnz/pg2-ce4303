"""Coordinator side of the distributed fusion: hand views to workers over the hub, merge what they return.

The grid, the poses and the parameters are fixed here, so every worker integrates into the same voxel grid.
Each worker returns one partial volume as sparse blocks; blocks are folded into a single accumulator volume
with the exact weighted average only after all the parts of a worker's result arrived, so a result that never
completes can be discarded without leaving a trace in the merged volume.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
import uuid
from dataclasses import dataclass, field

from astrea_common.config import ClusterConfig
from astrea_common.fusion import FusionSettings
from astrea_common.job_codec import BlockFromPayload, BuildAssignParts, JobOutcome, ResultAssembler
from astrea_common.link import Connection, HubServer
from astrea_common.messages import JobResult, JobStatus, Message, Role
from astrea_common.tsdf import GridSpec, TsdfVolume
from astrea_common.view_package import ViewPackage
from astrea_common.view_preprocess import PreprocessParams
from astrea_common.volume_blocks import MergeBlockInto

LOG = logging.getLogger("astrea.coordinator")
DEFAULT_JOB_TIMEOUT_S = 900.0
POLL_PERIOD_S = 0.1


class DistributedError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerStats:
    node: str
    views: int
    fusionSeconds: float
    roundTripSeconds: float
    blocks: int


@dataclass
class DistributedResult:
    volume: TsdfVolume
    keptPercent: list[float]
    workers: list[WorkerStats]
    wallSeconds: float
    mergeSeconds: float


@dataclass
class _PendingJob:
    node: str
    views: list[ViewPackage]
    sentAt: float
    finishedAt: float = 0.0
    assembler: ResultAssembler = field(default_factory=ResultAssembler)
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


def SplitEvenly(views: list[ViewPackage], workerCount: int) -> list[list[ViewPackage]]:
    """Round robin, so each worker gets views spread all around the object."""
    return [views[index::workerCount] for index in range(workerCount)]


class Coordinator:
    def __init__(self, config: ClusterConfig, sslContext: ssl.SSLContext | None = None) -> None:
        self.config = config
        self._pending: dict[str, _PendingJob] = {}
        self._hub = HubServer(config, onMessage=self._OnMessage, onPeerLost=self._OnPeerLost, sslContext=sslContext)

    async def Start(self) -> None:
        await self._hub.Start()

    async def Stop(self) -> None:
        await self._hub.Stop()

    def ConnectedWorkers(self) -> list[str]:
        return sorted(node for (node, role), connection in self._hub.peers.items() if role is Role.WORKER and connection.isOpen)

    async def WaitForWorkers(self, minimum: int, timeout: float) -> list[str]:
        deadline = time.monotonic() + timeout
        while len(self.ConnectedWorkers()) < minimum:
            if time.monotonic() > deadline:
                raise DistributedError(f"{len(self.ConnectedWorkers())} of {minimum} workers connected after {timeout:.0f} s: {self.ConnectedWorkers()}")
            await asyncio.sleep(POLL_PERIOD_S)
        return self.ConnectedWorkers()

    async def Fuse(
        self,
        views: list[ViewPackage],
        grid: GridSpec,
        params: PreprocessParams,
        fusion: FusionSettings,
        workers: list[str],
        jobTimeoutS: float = DEFAULT_JOB_TIMEOUT_S,
    ) -> DistributedResult:
        if not workers:
            raise DistributedError("no workers to fuse with")
        started = time.monotonic()
        scanId = uuid.uuid4().hex[:8]
        jobs = {f"{scanId}-{node}": _PendingJob(node, nodeViews, time.monotonic()) for node, nodeViews in zip(workers, SplitEvenly(views, len(workers))) if nodeViews}
        self._pending.update(jobs)
        try:
            await asyncio.gather(*(self._SendJob(jobId, job, grid, params, fusion) for jobId, job in jobs.items()))
            outcomes = await asyncio.wait_for(asyncio.gather(*(job.done for job in jobs.values())), jobTimeoutS)
        except asyncio.TimeoutError as error:
            raise DistributedError(f"workers did not finish within {jobTimeoutS:.0f} s") from error
        finally:
            for jobId in jobs:
                self._pending.pop(jobId, None)
        mergeStarted = time.monotonic()
        volume = await asyncio.to_thread(self._MergeOutcomes, grid, fusion, outcomes)
        stats = [WorkerStats(job.node, len(job.views), outcome.elapsedS, job.finishedAt - job.sentAt, len(outcome.blocks)) for job, outcome in zip(jobs.values(), outcomes)]
        return DistributedResult(volume, self._KeptPercentInOrder(views, outcomes), stats, time.monotonic() - started, time.monotonic() - mergeStarted)

    async def _SendJob(self, jobId: str, job: _PendingJob, grid: GridSpec, params: PreprocessParams, fusion: FusionSettings) -> None:
        parts = await asyncio.to_thread(BuildAssignParts, jobId, grid, params, fusion, job.views)
        job.sentAt = time.monotonic()
        for part in parts:
            await self._hub.Send(job.node, Role.WORKER, part)

    @staticmethod
    def _MergeOutcomes(grid: GridSpec, fusion: FusionSettings, outcomes: list[JobOutcome]) -> TsdfVolume:
        volume = TsdfVolume(grid, truncationMm=fusion.truncationMm)
        for outcome in outcomes:
            for payload in outcome.blocks:
                MergeBlockInto(volume, BlockFromPayload(payload))
        return volume

    @staticmethod
    def _KeptPercentInOrder(views: list[ViewPackage], outcomes: list[JobOutcome]) -> list[float]:
        byView = {index: kept for outcome in outcomes for index, kept in zip(outcome.viewIndices, outcome.keptPercent)}
        missing = [view.index for view in views if view.index not in byView]
        if missing:
            raise DistributedError(f"workers returned no statistics for views {missing}")
        return [byView[view.index] for view in views]

    async def _OnMessage(self, connection: Connection, message: Message) -> None:
        if not isinstance(message, JobResult):
            return
        job = self._pending.get(message.jobId)
        if job is None or job.node != connection.peer.node:
            LOG.warning("ignoring a result for unknown job '%s' from %s", message.jobId, connection.peer.node)
            return
        outcome = job.assembler.Add(message)
        if outcome is None or job.done.done():
            return
        job.finishedAt = time.monotonic()
        if outcome.status is JobStatus.OK:
            job.done.set_result(outcome)
        else:
            job.done.set_exception(DistributedError(f"{job.node} failed the job: {outcome.error}"))

    async def _OnPeerLost(self, connection: Connection, reason: str) -> None:
        for job in self._pending.values():
            if job.node == connection.peer.node and connection.peer.role is Role.WORKER and not job.done.done():
                job.done.set_exception(DistributedError(f"{job.node} disconnected during the job: {reason}"))
