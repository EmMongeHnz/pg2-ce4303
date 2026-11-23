"""Worker node: `uv run astrea-worker --name worker-b` connects to the hub and fuses the views it is assigned.

Launch the Pi with `OPENSSL_CONF=pki/openssl-chacha20.cnf` to force ChaCha20-Poly1305. `--max-grid-mib`
limits the size of the voxel grid this worker accepts (default: half of the available memory).
"""

import argparse
import asyncio
import logging
import sys

from astrea_common.config import ConfigError, LoadConfig
from astrea_common.messages import Role
from worker.service import WorkerService


def ParseArgs(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="astrea-worker", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.toml", help="cluster configuration (default: config.toml)")
    parser.add_argument("--name", required=True, help="node name from config.toml")
    parser.add_argument("--max-grid-mib", type=float, default=None, help="largest voxel grid to accept, in MiB")
    parser.add_argument("--verbose", action="store_true", help="log link and job events")
    return parser.parse_args(argv)


async def Serve(service: WorkerService) -> None:
    await service.Run()


def Main(argv: list[str] | None = None) -> int:
    args = ParseArgs(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    try:
        config = LoadConfig(args.config)
        maxGridBytes = None if args.max_grid_mib is None else int(args.max_grid_mib * 1024**2)
        service = WorkerService(config, args.name, Role.WORKER, maxGridBytes)
        print(f"worker '{args.name}' dialing {config.hub.address}:{config.hub.port} (grid limit {service.maxGridBytes / 1024**2:.0f} MiB)", flush=True)
        asyncio.run(Serve(service))
    except ConfigError as error:
        print(f"astrea-worker: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(Main())
