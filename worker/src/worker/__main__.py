"""Entry point of the worker skeleton; the real service arrives in M3/M4."""

import argparse
import sys

MILESTONE = "M3/M4"


def Main() -> int:
    parser = argparse.ArgumentParser(prog="worker", description=__doc__)
    parser.parse_args()
    print(f"worker: skeleton only, implemented in {MILESTONE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(Main())
