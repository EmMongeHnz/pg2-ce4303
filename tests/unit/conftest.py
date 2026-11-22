"""Shared fixtures: throw-away PKIs generated with pki/make-certs.sh (skipped when openssl is missing)."""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKE_CERTS = REPO_ROOT / "pki" / "make-certs.sh"
NODE_NAMES = ["host", "worker-b", "pi"]


@dataclass(frozen=True)
class Pki:
    directory: Path

    @property
    def ca(self) -> Path:
        return self.directory / "ca.crt"

    def Cert(self, node: str) -> Path:
        return self.directory / f"{node}.crt"

    def Key(self, node: str) -> Path:
        return self.directory / f"{node}.key"


def GeneratePki(directory: Path, nodes: list[str]) -> Pki:
    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    result = subprocess.run(["bash", str(MAKE_CERTS), "--out", str(directory), "--nodes", " ".join(nodes)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return Pki(directory)


@pytest.fixture(scope="session")
def pki(tmp_path_factory) -> Pki:
    return GeneratePki(tmp_path_factory.mktemp("pki"), NODE_NAMES)


@pytest.fixture(scope="session")
def foreignPki(tmp_path_factory) -> Pki:
    """A second CA issuing certificates with the same node names: valid chains, but not ours."""
    return GeneratePki(tmp_path_factory.mktemp("foreign-pki"), NODE_NAMES + ["intruder"])
