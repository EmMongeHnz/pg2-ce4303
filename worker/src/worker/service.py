"""Worker service: receives views over the secure link, fuses them into a partial TSDF and returns its blocks.

One job at a time. The fusion runs in a thread so the receive loop keeps answering heartbeats; a job whose
grid does not fit in memory is refused with an error result instead of being attempted.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from pathlib import Path

from astrea_common.config import ClusterConfig
from astrea_common.fusion import FuseViews
from astrea_common.job_codec import (
    AssignAssembler,
    AssignedJob,
    BlockToPayload,
    BuildErrorResult,
    BuildResultParts,
)
from astrea_common.link import Connection, NodeClient
from astrea_common.messages import InvalidMessage, JobAssign, JobResult, Message, Role
from astrea_common.tsdf import TsdfVolume
from astrea_common.volume_blocks import DEFAULT_TILE_VOXELS, IterateBlocks

LOG = logging.getLogger("astrea.worker")
MEMINFO_PATH = Path("/proc/meminfo")
MEMORY_SHARE_FOR_VOLUME = 0.5
FALLBACK_MAX_GRID_BYTES = 1024**3


def DefaultMaxGridBytes() -> int:
    """Half of the memory available now: the rest is for the integration temporaries and the encoded blocks."""
    try:
        for line in MEMINFO_PATH.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(int(line.split()[1]) * 1024 * MEMORY_SHARE_FOR_VOLUME)
    except (OSError, ValueError, IndexError):
        pass
    return FALLBACK_MAX_GRID_BYTES


def EncodeBlocks(volume: TsdfVolume):
    return [BlockToPayload(block) for block in IterateBlocks(volume, DEFAULT_TILE_VOXELS)]


class WorkerService:
    def __init__(
        self,
        config: ClusterConfig,
        nodeName: str,
        role: Role = Role.WORKER,
        maxGridBytes: int | None = None,
        sslContext: ssl.SSLContext | None = None,
    ) -> None:
        self.nodeName = nodeName
        self.maxGridBytes = maxGridBytes if maxGridBytes is not None else DefaultMaxGridBytes()
        self.jobsDone = 0
        self._assembler = AssignAssembler()
        self._runningJob: asyncio.Task | None = None
        self._client = NodeClient(config, nodeName, role, onMessage=self._OnMessage, sslContext=sslContext)

    async def Run(self) -> None:
        await self._client.Run()

    async def Stop(self) -> None:
        await self._client.Stop()
        if self._runningJob is not None:
            self._runningJob.cancel()

    async def WaitConnected(self, timeout: float) -> Connection:
        return await self._client.WaitConnected(timeout)

    async def _OnMessage(self, connection: Connection, message: Message) -> None:
        if not isinstance(message, JobAssign):
            return
        try:
            job = self._assembler.Add(message)
        except InvalidMessage as error:
            self._assembler = AssignAssembler()
            await self._Reply(connection, [BuildErrorResult(message.jobId, f"invalid job: {error}")])
            return
        if job is None:
            return
        self._assembler = AssignAssembler()
        if self._runningJob is not None and not self._runningJob.done():
            await self._Reply(connection, [BuildErrorResult(job.jobId, "worker busy")])
            return
        self._runningJob = asyncio.create_task(self._RunJob(connection, job))

    async def _RunJob(self, connection: Connection, job: AssignedJob) -> None:
        try:
            parts = await self._Process(job)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOG.exception("job %s failed", job.jobId)
            parts = [BuildErrorResult(job.jobId, f"{type(error).__name__}: {error}")]
        await self._Reply(connection, parts)

    async def _Process(self, job: AssignedJob) -> list[JobResult]:
        if job.grid.memoryBytes > self.maxGridBytes:
            need, have = job.grid.memoryBytes / 1024**2, self.maxGridBytes / 1024**2
            return [BuildErrorResult(job.jobId, f"grid needs {need:.0f} MiB, this worker allows {have:.0f} MiB")]
        started = time.monotonic()
        volume, keptPercent = await asyncio.to_thread(FuseViews, job.views, job.params, job.grid, job.fusion)
        fusionSeconds = time.monotonic() - started
        blocks = await asyncio.to_thread(EncodeBlocks, volume)
        self.jobsDone += 1
        LOG.info("job %s: %d views fused in %.1f s, %d blocks", job.jobId, len(job.views), fusionSeconds, len(blocks))
        return BuildResultParts(job.jobId, fusionSeconds, [view.index for view in job.views], keptPercent, blocks)

    async def _Reply(self, connection: Connection, parts: list[JobResult]) -> None:
        try:
            for part in parts:
                await connection.Send(part)
        except (OSError, ssl.SSLError, ConnectionError) as error:
            LOG.warning("could not return the result of %s: %s", parts[0].jobId, error)
