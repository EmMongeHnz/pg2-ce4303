"""Entry point of the coordinator skeleton; the real service arrives in M3/M4."""

import argparse
import sys

MILESTONE = "M3/M4"


def Main() -> int:
    parser = argparse.ArgumentParser(prog="coordinator", description=__doc__)
    parser.parse_args()
    print(f"coordinator: skeleton only, implemented in {MILESTONE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(Main())
