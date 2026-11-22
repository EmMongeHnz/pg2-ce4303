"""Link check for the secure network: `astrea-net-check serve` on the host, `connect` on the other nodes.

  astrea-net-check serve --config config.toml --expect worker-b pi:device-agent --duration 60
  astrea-net-check connect --config config.toml --name worker-b --duration 10

`serve` runs the hub, prints every peer with its negotiated TLS session and heartbeat round-trip time, and
with `--expect` exits 0 once every listed NODE[:ROLE] (role defaults to worker) is connected and measured.
`connect` dials the hub as one node, exchanges heartbeats for `--duration` seconds and exits 0 if the link
stayed up. Launch a node with `OPENSSL_CONF=pki/openssl-chacha20.cnf` to force ChaCha20-Poly1305.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from astrea_common.config import ClusterConfig, ConfigError, LoadConfig
from astrea_common.link import Connection, HubServer, NodeClient
from astrea_common.messages import Role

REPORT_PERIOD_S = 1.0
DEFAULT_CONFIG = "config.toml"
DEFAULT_CONNECT_TIMEOUT_S = 15.0
DEFAULT_LINGER_S = 5.0


def Report(text: str) -> None:
    print(text, flush=True)


def ParseExpected(items: list[str]) -> set[tuple[str, Role]]:
    expected = set()
    for item in items:
        name, _, roleText = item.partition(":")
        expected.add((name, Role(roleText or Role.WORKER.value)))
    return expected


def DescribePeer(connection: Connection) -> str:
    rtt = f"{connection.rttMs:.2f} ms" if connection.rttMs is not None else "no heartbeat yet"
    return f"{connection.peer.node} ({connection.peer.role.value}) {connection.peer.session}, rtt {rtt}"


async def Serve(config: ClusterConfig, expected: set[tuple[str, Role]], duration: float, linger: float) -> int:
    async def onConnected(connection: Connection) -> None:
        Report(f"connected: {DescribePeer(connection)}")

    async def onLost(connection: Connection, reason: str) -> None:
        Report(f"lost: {connection.peer.node} ({connection.peer.role.value}): {reason}")

    hub = HubServer(config, onPeerConnected=onConnected, onPeerLost=onLost)
    await hub.Start()
    Report(f"hub '{config.hub.node}' listening on {config.hub.bindAddress}:{config.hub.port}")
    deadline = time.monotonic() + duration
    try:
        while time.monotonic() < deadline:
            await asyncio.sleep(REPORT_PERIOD_S)
            peers = hub.peers
            for connection in peers.values():
                Report(f"peer: {DescribePeer(connection)}")
            if expected and all(key in peers and peers[key].rttMs is not None for key in expected):
                Report("all expected nodes are connected and exchanging heartbeats")
                await asyncio.sleep(linger)
                return 0
    finally:
        await hub.Stop()
    if expected:
        missing = sorted(f"{name}:{role.value}" for name, role in expected if (name, role) not in hub.peers)
        Report(f"timeout: missing or without heartbeat: {', '.join(missing) or 'none'}; rejected connections: {hub.rejections}")
        return 1
    return 0


async def Connect(config: ClusterConfig, nodeName: str, role: Role, duration: float, connectTimeout: float) -> int:
    client = NodeClient(config, nodeName, role)
    runner = asyncio.create_task(client.Run())
    try:
        try:
            connection = await client.WaitConnected(connectTimeout)
        except asyncio.TimeoutError:
            Report(f"could not connect to the hub at {config.hub.address}:{config.hub.port} within {connectTimeout:.0f} s")
            return 1
        Report(f"connected to {connection.peer.node} over {connection.peer.session}")
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and connection.isOpen:
            await asyncio.sleep(REPORT_PERIOD_S)
            Report(f"heartbeat rtt: {connection.rttMs:.2f} ms" if connection.rttMs is not None else "waiting for the first heartbeat echo")
        healthy = connection.isOpen and connection.rttMs is not None
        Report("link ok" if healthy else f"link failed: {connection.closeReason or 'no heartbeat echo received'}")
        return 0 if healthy else 1
    finally:
        await client.Stop()
        await runner


def BuildParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astrea-net-check", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="cluster configuration (default: config.toml)")
    parser.add_argument("--verbose", action="store_true", help="log link events")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the hub (host PC)")
    serve.add_argument("--expect", nargs="*", default=[], metavar="NODE[:ROLE]", help="exit 0 once all of these are connected")
    serve.add_argument("--duration", type=float, default=60.0, help="seconds to keep the hub up (default 60)")
    serve.add_argument("--linger", type=float, default=DEFAULT_LINGER_S, help="seconds to stay up after --expect is satisfied, so nodes can finish their check (default 5)")
    connect = commands.add_parser("connect", help="dial the hub as a node")
    connect.add_argument("--name", required=True, help="node name from config.toml")
    connect.add_argument("--role", default=Role.WORKER.value, choices=[role.value for role in Role if role is not Role.COORDINATOR])
    connect.add_argument("--duration", type=float, default=10.0, help="seconds to exchange heartbeats (default 10)")
    connect.add_argument("--connect-timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT_S)
    return parser


def Main(argv: list[str] | None = None) -> int:
    arguments = BuildParser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if arguments.verbose else logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    try:
        config = LoadConfig(arguments.config)
        if arguments.command == "serve":
            return asyncio.run(Serve(config, ParseExpected(arguments.expect), arguments.duration, arguments.linger))
        return asyncio.run(Connect(config, arguments.name, Role(arguments.role), arguments.duration, arguments.connect_timeout))
    except (ConfigError, ValueError, OSError) as error:
        print(f"astrea-net-check: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(Main())
