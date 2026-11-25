"""Workers and coordinator over real mutual-TLS links on loopback: the merged volume equals single-node fusion."""

import asyncio
from contextlib import asynccontextmanager

import numpy as np
import pytest

from astrea_common.fusion import FusionSettings, FuseViews
from astrea_common.job_codec import BuildAssignParts
from astrea_common.messages import Role
from astrea_common.tsdf import GridSpec
from astrea_common.view_preprocess import PreprocessParams
from coordinator.distributed import Coordinator, DistributedError, SplitEvenly
from sphere_scene import SMALL_GRID
from test_distributed_equivalence import Views
from test_secure_link import FreePort, MakeConfig, WaitUntil
from worker.service import WorkerService

NODES = {"host": {"roles": ["coordinator", "worker"]}, "worker-b": {"roles": ["worker"]}, "pi": {"roles": ["worker", "device-agent"]}}
PARAMS = PreprocessParams()
FUSION = FusionSettings(truncationMm=4.0 * SMALL_GRID.voxelMm)
CONNECT_S = 10.0


def SingleNode(views=None):
    return FuseViews(views if views is not None else Views(), PARAMS, SMALL_GRID, FUSION)


class SlowWorker(WorkerService):
    """Takes `delayS` before fusing, long enough for a test to pull the plug in the middle of the job."""

    delayS = 1.5

    async def _Process(self, job):
        await asyncio.sleep(self.delayS)
        return await super()._Process(job)


class StuckWorker(WorkerService):
    """Stays connected and answers heartbeats but never finishes a job."""

    async def _Process(self, job):
        await asyncio.sleep(3600)


@asynccontextmanager
async def Cluster(pki, workerNames: list[str], maxGridBytes: dict[str, int] | None = None, kinds: dict[str, type] | None = None):
    config = MakeConfig(pki, FreePort(), NODES)
    coordinator = Coordinator(config)
    await coordinator.Start()
    services = [(kinds or {}).get(name, WorkerService)(config, name, maxGridBytes=(maxGridBytes or {}).get(name)) for name in workerNames]
    runners = [asyncio.create_task(service.Run()) for service in services]
    try:
        await coordinator.WaitForWorkers(len(workerNames), CONNECT_S)
        yield coordinator, services
    finally:
        for service in services:
            await service.Stop()
        await asyncio.gather(*runners, return_exceptions=True)
        await coordinator.Stop()


def AssertSameVolume(distributed, single):
    assert np.allclose(distributed.weight, single.weight, rtol=1e-5)
    assert np.abs(distributed.tsdf - single.tsdf).max() < 1e-4
    assert np.abs(distributed.color - single.color).max() < 1e-2


def test_split_evenly_interleaves_views():
    views = Views()
    parts = SplitEvenly(views, 3)
    assert [len(part) for part in parts] == [8, 8, 8]
    assert [view.index for view in parts[1]] == list(range(1, 24, 3))
    assert sorted(view.index for part in parts for view in part) == list(range(24))


@pytest.mark.parametrize("workerNames", [["host"], ["host", "worker-b", "pi"]])
def test_distributed_volume_equals_single_node_fusion(pki, workerNames):
    async def scenario():
        views = Views()
        async with Cluster(pki, workerNames) as (coordinator, services):
            result = await coordinator.Fuse(views, SMALL_GRID, PARAMS, FUSION, coordinator.ConnectedWorkers())
            return result, [service.jobsDone for service in services]

    result, jobsDone = asyncio.run(scenario())
    single, singleKept = SingleNode()
    AssertSameVolume(result.volume, single)
    np.testing.assert_allclose(result.keptPercent, singleKept)
    assert jobsDone == [1] * len(workerNames)
    assert sorted(stats.node for stats in result.workers) == sorted(workerNames)
    assert sum(stats.views for stats in result.workers) == 24


def test_fewer_views_than_workers_still_merges_exactly(pki):
    async def scenario():
        views = Views()[:2]
        async with Cluster(pki, ["host", "worker-b", "pi"]) as (coordinator, _):
            return await coordinator.Fuse(views, SMALL_GRID, PARAMS, FUSION, coordinator.ConnectedWorkers()), views

    result, views = asyncio.run(scenario())
    AssertSameVolume(result.volume, SingleNode(views)[0])
    assert len(result.workers) == 2


def test_a_worker_that_refuses_the_grid_hands_its_views_to_the_others(pki):
    async def scenario():
        async with Cluster(pki, ["host", "worker-b"], maxGridBytes={"worker-b": 1024}) as (coordinator, _):
            return await coordinator.Fuse(Views(), SMALL_GRID, PARAMS, FUSION, coordinator.ConnectedWorkers())

    result = asyncio.run(scenario())
    single, singleKept = SingleNode()
    AssertSameVolume(result.volume, single)
    np.testing.assert_allclose(result.keptPercent, singleKept)
    assert [stats.node for stats in result.workers] == ["host", "host"], "the host ran its own share and then the views of worker-b"
    assert sum(stats.views for stats in result.workers) == 24
    assert len(result.failures) == 1 and "worker-b failed the job: grid needs" in result.failures[0]


def test_when_every_worker_refuses_the_grid_the_run_fails_and_says_why(pki):
    async def scenario():
        async with Cluster(pki, ["host", "worker-b"], maxGridBytes={"host": 1024, "worker-b": 1024}) as (coordinator, _):
            await coordinator.Fuse(Views(), SMALL_GRID, PARAMS, FUSION, coordinator.ConnectedWorkers())

    with pytest.raises(DistributedError, match="no workers left to fuse 24 of 24 views.*grid needs"):
        asyncio.run(scenario())


def test_killing_a_worker_in_the_middle_of_the_job_still_finishes_with_the_same_volume(pki):
    async def scenario():
        views = Views()
        async with Cluster(pki, ["host", "worker-b", "pi"], kinds={"worker-b": SlowWorker}) as (coordinator, services):
            fusing = asyncio.create_task(coordinator.Fuse(views, SMALL_GRID, PARAMS, FUSION, coordinator.ConnectedWorkers()))
            await WaitUntil(lambda: any(job.node == "worker-b" and job.sentAt for job in coordinator._pending.values()))
            await asyncio.sleep(0.3)
            await services[1].Stop()
            return await fusing

    result = asyncio.run(scenario())
    single, singleKept = SingleNode()
    AssertSameVolume(result.volume, single)
    np.testing.assert_allclose(result.keptPercent, singleKept)
    assert sum(stats.views for stats in result.workers) == 24
    assert "worker-b" not in [stats.node for stats in result.workers]
    assert len(result.failures) == 1 and result.failures[0].startswith("worker-b: worker-b disconnected")


def test_a_worker_that_never_finishes_is_dropped_after_the_job_timeout(pki):
    async def scenario():
        async with Cluster(pki, ["host", "worker-b"], kinds={"worker-b": StuckWorker}) as (coordinator, _):
            return await coordinator.Fuse(Views(), SMALL_GRID, PARAMS, FUSION, coordinator.ConnectedWorkers(), jobTimeoutS=1.0)

    result = asyncio.run(scenario())
    AssertSameVolume(result.volume, SingleNode()[0])
    assert result.failures == ["worker-b: worker-b did not finish within 1 s"]


def test_losing_the_only_worker_fails_with_the_views_it_left_unfused(pki):
    async def scenario():
        async with Cluster(pki, ["worker-b"], kinds={"worker-b": SlowWorker}) as (coordinator, services):
            fusing = asyncio.create_task(coordinator.Fuse(Views(), SMALL_GRID, PARAMS, FUSION, ["worker-b"]))
            await WaitUntil(lambda: any(job.sentAt for job in coordinator._pending.values()))
            await asyncio.sleep(0.3)
            await services[0].Stop()
            await fusing

    with pytest.raises(DistributedError, match="no workers left to fuse 24 of 24 views .*disconnected"):
        asyncio.run(scenario())


def test_waiting_for_more_workers_than_connected_times_out(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort(), NODES)
        coordinator = Coordinator(config)
        await coordinator.Start()
        try:
            await coordinator.WaitForWorkers(1, 0.5)
        finally:
            await coordinator.Stop()

    with pytest.raises(DistributedError, match="0 of 1 workers"):
        asyncio.run(scenario())


def test_fusing_without_workers_is_an_error(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort(), NODES)
        coordinator = Coordinator(config)
        await coordinator.Start()
        try:
            await coordinator.Fuse(Views(), SMALL_GRID, PARAMS, FUSION, [])
        finally:
            await coordinator.Stop()

    with pytest.raises(DistributedError):
        asyncio.run(scenario())


def test_link_stays_alive_while_a_worker_computes_for_longer_than_the_heartbeat_timeout(pki):
    from test_secure_link import HEARTBEAT_TIMEOUT_S

    bigGrid = GridSpec((-63.0, -63.0, -63.0), 1.0, (126, 126, 126))
    fusion = FusionSettings(truncationMm=4.0)

    async def scenario():
        views = Views()[:6]
        async with Cluster(pki, ["worker-b"]) as (coordinator, _):
            result = await coordinator.Fuse(views, bigGrid, PARAMS, fusion, ["worker-b"])
            return result, views

    result, views = asyncio.run(scenario())
    assert result.workers[0].fusionSeconds > HEARTBEAT_TIMEOUT_S, "the job must outlast the heartbeat timeout for this test to mean anything"
    AssertSameVolume(result.volume, FuseViews(views, PARAMS, bigGrid, fusion)[0])


def test_statistics_follow_the_order_of_the_views_even_when_sessions_repeat_view_indices(pki):
    async def scenario():
        views = Views() + Views()
        async with Cluster(pki, ["host", "worker-b", "pi"]) as (coordinator, _):
            return await coordinator.Fuse(views, SMALL_GRID, PARAMS, FUSION, coordinator.ConnectedWorkers()), views

    result, views = asyncio.run(scenario())
    single, singleKept = SingleNode(views)
    np.testing.assert_allclose(result.keptPercent, singleKept)
    assert len(set(round(value, 3) for value in singleKept)) > 1, "the views must differ for this test to mean anything"
    AssertSameVolume(result.volume, single)


def test_worker_reports_the_bytes_it_received_and_returned(pki):
    async def scenario():
        async with Cluster(pki, ["host", "worker-b"]) as (coordinator, _):
            return await coordinator.Fuse(Views(), SMALL_GRID, PARAMS, FUSION, coordinator.ConnectedWorkers())

    result = asyncio.run(scenario())
    assert all(stats.sentBytes > 0 and stats.receivedBytes > 0 for stats in result.workers)


async def RunSecondCoordinatorOn(config, views, grid, fusion):
    second = Coordinator(config)
    await second.Start()
    try:
        await second.WaitForWorkers(1, CONNECT_S)
        return await second.Fuse(views, grid, PARAMS, fusion, ["worker-b"])
    finally:
        await second.Stop()


def test_worker_recovers_from_a_coordinator_that_stopped_halfway_through_sending_a_job(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort(), NODES)
        service = WorkerService(config, "worker-b")
        runner = asyncio.create_task(service.Run())
        try:
            first = Coordinator(config)
            await first.Start()
            await first.WaitForWorkers(1, CONNECT_S)
            parts = BuildAssignParts("abandoned", SMALL_GRID, PARAMS, FUSION, Views()[:3], maxPartBytes=1)
            assert len(parts) == 3
            await first._hub.Send("worker-b", Role.WORKER, parts[0])
            await asyncio.sleep(0.2)
            await first.Stop()
            return await RunSecondCoordinatorOn(config, Views(), SMALL_GRID, FUSION)
        finally:
            await service.Stop()
            await asyncio.gather(runner, return_exceptions=True)

    AssertSameVolume(asyncio.run(scenario()).volume, SingleNode()[0])


def test_worker_still_computing_for_a_dead_coordinator_does_not_refuse_the_next_one(pki):
    bigGrid = GridSpec((-63.0, -63.0, -63.0), 1.0, (126, 126, 126))
    bigFusion = FusionSettings(truncationMm=4.0)

    async def scenario():
        config = MakeConfig(pki, FreePort(), NODES)
        service = WorkerService(config, "worker-b")
        runner = asyncio.create_task(service.Run())
        try:
            first = Coordinator(config)
            await first.Start()
            await first.WaitForWorkers(1, CONNECT_S)
            for part in BuildAssignParts("stale", bigGrid, PARAMS, bigFusion, Views()[:6]):
                await first._hub.Send("worker-b", Role.WORKER, part)
            await asyncio.sleep(0.5)
            await first.Stop()
            return await RunSecondCoordinatorOn(config, Views(), SMALL_GRID, FUSION)
        finally:
            await service.Stop()
            await asyncio.gather(runner, return_exceptions=True)

    AssertSameVolume(asyncio.run(scenario()).volume, SingleNode()[0])


def test_a_worker_lost_after_its_job_was_given_up_leaves_no_unread_exception(pki, caplog):
    import gc
    import logging
    from types import SimpleNamespace

    from coordinator.distributed import _PendingJob

    async def scenario():
        coordinator = Coordinator(MakeConfig(pki, FreePort(), NODES))
        job = _PendingJob("worker-b", [0, 1], [], 0.0)
        coordinator._pending["lost"] = job
        Coordinator._RetireJob(job)
        lostPeer = SimpleNamespace(peer=SimpleNamespace(node="worker-b", role=Role.WORKER))
        await coordinator._OnPeerLost(lostPeer, "connection reset")
        return job

    with caplog.at_level(logging.ERROR, logger="asyncio"):
        job = asyncio.run(scenario())
        del job
        gc.collect()
    assert not [record for record in caplog.records if "never retrieved" in record.getMessage()]
