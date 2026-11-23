"""Distributed reconstruction: `astrea-coordinator reconstruct <sessions>` fuses the views on the connected workers.

Start the hub with this command, then launch `astrea-worker --name <node>` on each worker machine (they
wait for the hub and reconnect on their own). The output is the same as `astrea-reconstruct`: lod-{full,high,low}.glb
and report.json.

    uv run astrea-coordinator reconstruct out/scans/cup --min-workers 3 --config config.toml
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from astrea_common.config import ConfigError, LoadConfig
from coordinator.distributed import DEFAULT_JOB_TIMEOUT_S, Coordinator, DistributedError, DistributedResult
from coordinator.reconstruct import (
    AddPipelineArguments,
    ExportReconstruction,
    ParamsFromArgs,
    PrepareScan,
    PrintReport,
    SettingsFromArgs,
)

DEFAULT_WAIT_S = 120.0


def BuildParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astrea-coordinator", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.toml", help="cluster configuration (default: config.toml)")
    parser.add_argument("--verbose", action="store_true", help="log link and job events")
    commands = parser.add_subparsers(dest="command", required=True)
    reconstruct = commands.add_parser("reconstruct", help="reconstruct saved sessions on the connected workers")
    reconstruct.add_argument("sessions", type=Path, nargs="+", help="session directories written by astrea-capture")
    reconstruct.add_argument("--out", type=Path, default=None, help="output directory (default: <first session>/model)")
    reconstruct.add_argument("--min-workers", type=int, default=1, help="wait for at least this many workers")
    reconstruct.add_argument("--wait", type=float, default=DEFAULT_WAIT_S, help="seconds to wait for the workers (default 120)")
    reconstruct.add_argument("--job-timeout", type=float, default=DEFAULT_JOB_TIMEOUT_S, help="seconds before giving up on the workers (default 900)")
    AddPipelineArguments(reconstruct)
    return parser


def PrintWorkers(result: DistributedResult) -> None:
    for stats in result.workers:
        print(f"  {stats.node}: {stats.views} views, fusion {stats.fusionSeconds:.1f} s, {stats.blocks} blocks")
    print(f"  merge {result.mergeSeconds:.1f} s, total {result.wallSeconds:.1f} s")


async def RunReconstruction(args: argparse.Namespace) -> int:
    config = LoadConfig(args.config)
    params, settings = ParamsFromArgs(args), SettingsFromArgs(args)
    outDir = args.out or args.sessions[0] / "model"
    print("preparing the scan (poses, grid)...", flush=True)
    scan = PrepareScan(args.sessions, params, None, settings)
    views = list(scan.viewSource())
    shape = scan.grid.shape
    print(f"{len(views)} views, grid {shape[0]}x{shape[1]}x{shape[2]} at {scan.grid.voxelMm:.2f} mm", flush=True)
    coordinator = Coordinator(config)
    await coordinator.Start()
    try:
        print(f"hub listening on {config.hub.bindAddress}:{config.hub.port}; waiting for {args.min_workers} worker(s)...", flush=True)
        workers = await coordinator.WaitForWorkers(args.min_workers, args.wait)
        print(f"fusing on {', '.join(workers)}", flush=True)
        result = await coordinator.Fuse(views, scan.grid, params, settings.Fusion(), workers, args.job_timeout)
    finally:
        await coordinator.Stop()
    PrintWorkers(result)
    report = ExportReconstruction(result.volume, result.keptPercent, scan, params, settings, result.wallSeconds, outDir)
    PrintReport(report, outDir)
    return 0 if report.passesGate else 1


def Main(argv: list[str] | None = None) -> int:
    args = BuildParser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    try:
        return asyncio.run(RunReconstruction(args))
    except (ConfigError, DistributedError, ValueError, OSError) as error:
        print(f"astrea-coordinator: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(Main())
