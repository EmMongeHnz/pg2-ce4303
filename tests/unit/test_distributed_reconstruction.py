"""Workers and coordinator over real mutual-TLS links on loopback: the merged volume equals single-node fusion."""

import asyncio
from contextlib import asynccontextmanager

import numpy as np
import pytest

from astrea_common.fusion import FusionSettings, FuseViews
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


@asynccontextmanager
async def Cluster(pki, workerNames: list[str], maxGridBytes: dict[str, int] | None = None):
    config = MakeConfig(pki, FreePort(), NODES)
    coordinator = Coordinator(config)
    await coordinator.Start()
    services = [WorkerService(config, name, maxGridBytes=(maxGridBytes or {}).get(name)) for name in workerNames]
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


def test_worker_refuses_a_grid_larger_than_its_memory_limit(pki):
    async def scenario():
        async with Cluster(pki, ["host", "worker-b"], maxGridBytes={"worker-b": 1024}) as (coordinator, _):
            await coordinator.Fuse(Views(), SMALL_GRID, PARAMS, FUSION, coordinator.ConnectedWorkers())

    with pytest.raises(DistributedError, match="worker-b failed the job: grid needs"):
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
    from astrea_common.tsdf import GridSpec
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
