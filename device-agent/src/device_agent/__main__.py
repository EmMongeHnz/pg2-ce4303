"""Launcher of the device agent, which is written in C (`net/src/astrea-agent.c`).

    uv run astrea-device-agent --name pi --models-dir out/models --verbose

The arguments go straight to the C program; see `net/build/astrea-device-agent` without arguments for the list.
"""

import os
import sys

from astrea_common.net_daemon import DaemonError, FindProgram


def Main() -> int:
    try:
        program = FindProgram("astrea-device-agent", "ASTREA_DEVICE_AGENT")
    except DaemonError as error:
        print(f"astrea-device-agent: {error}", file=sys.stderr)
        return 2
    os.execv(program, [str(program), *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(Main())
