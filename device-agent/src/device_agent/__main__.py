"""Entry point of the device-agent skeleton; the real service arrives in M5."""

import argparse
import sys

MILESTONE = "M5"


def Main() -> int:
    parser = argparse.ArgumentParser(prog="device-agent", description=__doc__)
    parser.parse_args()
    print(f"device-agent: skeleton only, implemented in {MILESTONE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(Main())
