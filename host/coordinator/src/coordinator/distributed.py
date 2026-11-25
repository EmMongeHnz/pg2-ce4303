"""Coordinator side of the distributed fusion: hand views to workers over the hub, merge what they return.

The grid, the poses and the parameters are fixed here, so every worker integrates into the same voxel grid.
Each worker returns one partial volume as sparse blocks; blocks are folded into a single accumulator volume
with the exact weighted average only after all the parts of a worker's result arrived, so a result that never
completes can be discarded without leaving a trace in the merged volume.

A worker that disconnects, refuses the job, answers with bad statistics or does not finish in time is dropped
for the rest of the run and its views go to the workers that are left in a new round. The merge does not depend
on which worker fused which view, so the result is the same as if nothing had failed.
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
from astrea_common.job_codec import BlockFromPayload, BlockPayloadBytes, BuildAssignParts, JobOutcome, ResultAssembler, ViewPayloadBytes
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


JOB_FAILURES = (DistributedError, ConnectionError, OSError, asyncio.TimeoutError)


@dataclass(frozen=True)
class WorkerStats:
    node: str
    views: int
    fusionSeconds: float
    roundTripSeconds: float
    blocks: int
    sentBytes: int
    receivedBytes: int


@dataclass
class DistributedResult:
    volume: TsdfVolume
    keptPercent: list[float]
    workers: list[WorkerStats]
    wallSeconds: float
    mergeSeconds: float
    failures: list[str] = field(default_factory=list)


@dataclass
class _PendingJob:
    node: str
    positions: list[int]
    views: list[ViewPackage]
    sentAt: float
    sentBytes: int = 0
    finishedAt: float = 0.0
    assembler: ResultAssembler = field(default_factory=ResultAssembler)
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


def SplitEvenly(items: list, workerCount: int) -> list[list]:
    """Round robin, so each worker gets views spread all around the object."""
    return [items[index::workerCount] for index in range(workerCount)]


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
        remaining = list(range(len(views)))
        completed: list[tuple[_PendingJob, JobOutcome]] = []
        failures: list[str] = []
        failed: set[str] = set()
        attempt = 0
        while remaining:
            connected = self.ConnectedWorkers()
            candidates = [node for node in workers if node not in failed and node in connected]
            if not candidates:
                reason = "; ".join(failures) or "none of them is connected"
                raise DistributedError(f"no workers left to fuse {len(remaining)} of {len(views)} views ({reason})")
            shares = SplitEvenly(remaining, len(candidates))
            jobs = {
                f"{scanId}-{attempt}-{node}": _PendingJob(node, positions, [views[position] for position in positions], time.monotonic())
                for node, positions in zip(candidates, shares)
                if positions
            }
            attempt += 1
            remaining = await self._RunRound(jobs, grid, params, fusion, jobTimeoutS, completed, failures, failed)
        mergeStarted = time.monotonic()
        outcomes = [outcome for _, outcome in completed]
        volume = await asyncio.to_thread(self._MergeOutcomes, grid, fusion, outcomes)
        stats = [
            WorkerStats(job.node, len(job.views), outcome.elapsedS, job.finishedAt - job.sentAt, len(outcome.blocks), job.sentBytes, sum(BlockPayloadBytes(block) for block in outcome.blocks))
            for job, outcome in completed
        ]
        keptPercent = self._KeptPercentInOrder(len(views), [job for job, _ in completed], outcomes)
        return DistributedResult(volume, keptPercent, stats, time.monotonic() - started, time.monotonic() - mergeStarted, failures)

    async def _RunRound(
        self,
        jobs: dict[str, _PendingJob],
        grid: GridSpec,
        params: PreprocessParams,
        fusion: FusionSettings,
        jobTimeoutS: float,
        completed: list[tuple[_PendingJob, JobOutcome]],
        failures: list[str],
        failed: set[str],
    ) -> list[int]:
        """Runs one job per worker at once and returns the positions of the views whose job failed."""
        self._pending.update(jobs)
        try:
            results = await asyncio.gather(*(self._RunJob(jobId, job, grid, params, fusion, jobTimeoutS) for jobId, job in jobs.items()), return_exceptions=True)
        finally:
            for jobId in jobs:
                self._pending.pop(jobId, None)
        lost: list[int] = []
        for job, result in zip(jobs.values(), results):
            if isinstance(result, JobOutcome):
                completed.append((job, result))
                continue
            if not isinstance(result, JOB_FAILURES):
                raise result
            reason = str(result) or type(result).__name__
            LOG.warning("%s failed (%s); its %d views go to the other workers", job.node, reason, len(job.positions))
            failed.add(job.node)
            failures.append(f"{job.node}: {reason}")
            lost.extend(job.positions)
        return sorted(lost)

    async def _RunJob(self, jobId: str, job: _PendingJob, grid: GridSpec, params: PreprocessParams, fusion: FusionSettings, timeoutS: float) -> JobOutcome:
        try:
            await self._SendJob(jobId, job, grid, params, fusion)
            try:
                outcome = await asyncio.wait_for(job.done, timeoutS)
            except asyncio.TimeoutError as error:
                raise DistributedError(f"{job.node} did not finish within {timeoutS:.0f} s") from error
        finally:
            self._RetireJob(job)
        if len(outcome.keptPercent) != len(job.positions):
            raise DistributedError(f"{job.node} returned statistics for {len(outcome.keptPercent)} of its {len(job.positions)} views")
        return outcome

    @staticmethod
    def _RetireJob(job: _PendingJob) -> None:
        """Nobody waits for this job any more: a later disconnection or result must not leave an unread exception."""
        if not job.done.done():
            job.done.cancel()
        elif not job.done.cancelled():
            job.done.exception()

    async def _SendJob(self, jobId: str, job: _PendingJob, grid: GridSpec, params: PreprocessParams, fusion: FusionSettings) -> None:
        parts = await asyncio.to_thread(BuildAssignParts, jobId, grid, params, fusion, job.views)
        job.sentAt = time.monotonic()
        job.sentBytes = sum(ViewPayloadBytes(view) for part in parts for view in part.views)
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
    def _KeptPercentInOrder(viewCount: int, jobs: list[_PendingJob], outcomes: list[JobOutcome]) -> list[float]:
        """Statistics come back in the order the views were sent, so they are matched by position, not by view index."""
        kept = [0.0] * viewCount
        for job, outcome in zip(jobs, outcomes):
            if len(outcome.keptPercent) != len(job.positions):
                raise DistributedError(f"{job.node} returned statistics for {len(outcome.keptPercent)} of its {len(job.positions)} views")
            for position, percent in zip(job.positions, outcome.keptPercent):
                kept[position] = percent
        return kept

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
