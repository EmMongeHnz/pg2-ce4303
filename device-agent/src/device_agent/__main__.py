"""Device agent: runs on the Raspberry Pi, next to the holo-io driver and the hologram renderer.

    OPENSSL_CONF=pki/openssl-chacha20.cnf uv run astrea-device-agent --name pi
    renderer/build/renderer --mode hologram --up-axis z --model out/models/cup.glb \\
        --control-socket /tmp/astrea-hologram.sock --models-dir out/models

It forwards the button events of /dev/holo-input to the host and shows what the host says: the camera state goes
to the renderer through --renderer-socket and the pushed models are installed in --models-dir.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from astrea_common.config import ConfigError, LoadConfig
from device_agent.service import DeviceAgentService


def ParseArgs(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="astrea-device-agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.toml", help="cluster configuration (default: config.toml)")
    parser.add_argument("--name", required=True, help="node name from config.toml")
    parser.add_argument("--device", default="/dev/holo-input", help="input device (default: /dev/holo-input)")
    parser.add_argument("--no-device", action="store_true", help="do not read an input device (only show the host's state)")
    parser.add_argument("--renderer-socket", default="/tmp/astrea-hologram.sock", help="Unix socket the renderer connects to")
    parser.add_argument("--models-dir", default="out/models", help="where pushed models are installed as <modelId>.glb")
    parser.add_argument("--verbose", action="store_true", help="log link, model and device events")
    return parser.parse_args(argv)


def Main(argv: list[str] | None = None) -> int:
    args = ParseArgs(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    try:
        config = LoadConfig(args.config)
        service = DeviceAgentService(
            config, args.name, Path(args.renderer_socket), Path(args.models_dir), None if args.no_device else Path(args.device)
        )
        print(f"device agent '{args.name}' dialing {config.hub.address}:{config.hub.devicePort}, renderer socket {args.renderer_socket}", flush=True)
        asyncio.run(service.Run())
    except ConfigError as error:
        print(f"astrea-device-agent: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(Main())
