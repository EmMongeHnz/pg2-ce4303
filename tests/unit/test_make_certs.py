"""pki/make-certs.sh produces a CA and per-node certificates that chain correctly and keep keys private."""

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "pki" / "make-certs.sh"
NODES = ["host", "worker-b", "pi"]

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not installed")


def RunScript(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)


def OpenSsl(*args: str) -> str:
    return subprocess.run(["openssl", *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def certDir(tmp_path: Path) -> Path:
    outDir = tmp_path / "certs"
    result = RunScript("--out", str(outDir), "--ip", "host=192.168.1.10")
    assert result.returncode == 0, result.stderr
    return outDir


def test_every_node_certificate_chains_to_the_ca(certDir: Path):
    for node in NODES:
        OpenSsl("verify", "-CAfile", str(certDir / "ca.crt"), str(certDir / f"{node}.crt"))


def test_node_name_and_ip_are_subject_alternative_names(certDir: Path):
    text = OpenSsl("x509", "-in", str(certDir / "host.crt"), "-noout", "-text")
    assert "DNS:host" in text
    assert "IP Address:192.168.1.10" in text


def test_node_certificates_allow_both_tls_roles_and_are_not_cas(certDir: Path):
    text = OpenSsl("x509", "-in", str(certDir / "pi.crt"), "-noout", "-text")
    assert "TLS Web Server Authentication" in text
    assert "TLS Web Client Authentication" in text
    assert "CA:FALSE" in text


def test_keys_are_owner_only(certDir: Path):
    for key in certDir.glob("*.key"):
        assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_certificate_of_another_ca_does_not_verify(certDir: Path, tmp_path: Path):
    otherDir = tmp_path / "other"
    assert RunScript("--out", str(otherDir)).returncode == 0
    result = subprocess.run(["openssl", "verify", "-CAfile", str(otherDir / "ca.crt"), str(certDir / "host.crt")], capture_output=True, text=True)
    assert result.returncode != 0


def test_existing_files_are_not_overwritten_without_force(certDir: Path):
    before = (certDir / "ca.key").read_bytes()
    assert RunScript("--out", str(certDir)).returncode != 0
    assert (certDir / "ca.key").read_bytes() == before
    assert RunScript("--out", str(certDir), "--force").returncode == 0
    assert (certDir / "ca.key").read_bytes() != before


def test_invalid_node_name_is_rejected(tmp_path: Path):
    result = RunScript("--out", str(tmp_path / "bad"), "--nodes", "Bad_Name")
    assert result.returncode != 0
    assert "invalid node name" in result.stderr
