"""astrea-net-check: the hub and two nodes verify each other over loopback, and failures give exit code 1."""

import asyncio

import pytest

from astrea_common.messages import Role
from astrea_common.net_check import Connect, ParseExpected, Serve
from test_secure_link import FreePort, MakeConfig


def test_expected_nodes_default_to_the_worker_role():
    assert ParseExpected(["worker-b", "pi:device-agent"]) == {("worker-b", Role.WORKER), ("pi", Role.DEVICE_AGENT)}


def test_unknown_expected_role_is_rejected():
    with pytest.raises(ValueError):
        ParseExpected(["pi:admin"])


def test_hub_and_two_nodes_pass_the_link_check(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        expected = {("worker-b", Role.WORKER), ("pi", Role.DEVICE_AGENT)}
        return await asyncio.gather(
            Serve(config, expected, duration=20.0, linger=4.0),
            Connect(config, "worker-b", Role.WORKER, duration=2.5, connectTimeout=10.0),
            Connect(config, "pi", Role.DEVICE_AGENT, duration=2.5, connectTimeout=10.0),
        )

    assert asyncio.run(scenario()) == [0, 0, 0]


def test_hub_reports_failure_when_an_expected_node_never_arrives(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        return await Serve(config, {("pi", Role.WORKER)}, duration=1.2, linger=0.0)

    assert asyncio.run(scenario()) == 1


def test_node_reports_failure_when_the_hub_is_unreachable(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        return await Connect(config, "pi", Role.WORKER, duration=1.0, connectTimeout=0.6)

    assert asyncio.run(scenario()) == 1


def test_node_without_duration_runs_until_the_hub_closes_and_still_reports_ok(pki):
    from test_secure_link import RunningHub, WaitUntil

    async def scenario():
        config = MakeConfig(pki, FreePort())
        async with RunningHub(config) as hub:
            check = asyncio.create_task(Connect(config, "worker-b", Role.WORKER, duration=None, connectTimeout=10.0))
            await WaitUntil(lambda: ("worker-b", Role.WORKER) in hub.peers and hub.peers[("worker-b", Role.WORKER)].rttMs is not None)
            await asyncio.sleep(1.2)
        return await asyncio.wait_for(check, 10)

    assert asyncio.run(scenario()) == 0


def test_node_starting_long_after_the_others_still_passes(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        expected = {("worker-b", Role.WORKER), ("pi", Role.DEVICE_AGENT)}
        serve = asyncio.create_task(Serve(config, expected, duration=30.0, linger=3.0))
        early = asyncio.create_task(Connect(config, "worker-b", Role.WORKER, duration=None, connectTimeout=10.0))
        await asyncio.sleep(2.5)
        late = asyncio.create_task(Connect(config, "pi", Role.DEVICE_AGENT, duration=None, connectTimeout=10.0))
        return await asyncio.gather(serve, early, late)

    assert asyncio.run(scenario()) == [0, 0, 0]
