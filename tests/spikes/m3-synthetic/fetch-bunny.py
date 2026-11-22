#!/usr/bin/env python3
"""Download the Stanford bunny reconstructions for the M3 synthetic test.

Usage:
    uv run tests/spikes/m3-synthetic/fetch-bunny.py

Fetches bunny.tar.gz into out/datasets/stanford-bunny/ (ignored by git), checks its SHA-256 and
extracts only bunny/reconstruction/*.ply into a new `extracted/` directory next to it.

The model is not versioned. Terms (https://graphics.stanford.edu/data/3Dscanrep/): free use for
research, free redistribution allowed, and the source must be acknowledged:
"Source: Stanford University Computer Graphics Laboratory".
"""

from __future__ import annotations

import argparse
import hashlib
import tarfile
import urllib.request
from pathlib import Path

URL = "http://graphics.stanford.edu/pub/3Dscanrep/bunny.tar.gz"
SHA256 = "a5720bd96d158df403d153381b8411a727a1d73cff2f33dc9b212d6f75455b84"
MEMBER_PREFIX = "bunny/reconstruction/"
DEFAULT_DIR = Path("out/datasets/stanford-bunny")


def ParseArgs() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    return parser.parse_args()


def Sha256Of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def Download(archive: Path) -> None:
    print(f"downloading {URL}")
    urllib.request.urlretrieve(URL, archive)
    if Sha256Of(archive) != SHA256:
        archive.unlink()
        raise SystemExit("SHA-256 mismatch: the archive was removed. Check the source before updating the hash.")


def Extract(archive: Path, target: Path) -> None:
    target.mkdir()
    with tarfile.open(archive) as tar:
        members = [m for m in tar.getmembers() if m.isfile() and m.name.startswith(MEMBER_PREFIX)]
        tar.extractall(target, members=members, filter="data")


def Main() -> None:
    args = ParseArgs()
    args.dir.mkdir(parents=True, exist_ok=True)
    archive = args.dir / "bunny.tar.gz"
    if not archive.exists() or Sha256Of(archive) != SHA256:
        Download(archive)
    extracted = args.dir / "extracted"
    if not extracted.exists():
        Extract(archive, extracted)
    print(f"ready: {extracted / MEMBER_PREFIX / 'bun_zipper.ply'}")
    print("Source: Stanford University Computer Graphics Laboratory")


if __name__ == "__main__":
    Main()
