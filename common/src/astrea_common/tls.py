"""TLS 1.3 contexts with mandatory mutual authentication against the private CA.

Both sides only trust `ca.crt` (never the system store) and refuse any other TLS version. Nodes dialing the
hub verify its certificate against the hub node name, so a certificate of another node is not accepted
as the hub. The hub learns who dialed from the SAN of the client certificate (`PeerNodeName`).

Python's `ssl` cannot choose TLS 1.3 cipher suites. To make a process use ChaCha20-Poly1305 only (the Pi 3
has no AES acceleration) start it with `OPENSSL_CONF=pki/openssl-chacha20.cnf`.
"""

from __future__ import annotations

import ssl
from pathlib import Path

from astrea_common.config import ClusterConfig
from astrea_common.messages import NODE_NAME_PATTERN


class PeerIdentityError(Exception):
    """The peer certificate does not carry exactly one valid node name."""


def _NewContext(purpose: int, caFile: Path, certFile: Path, keyFile: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(purpose)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(certfile=str(certFile), keyfile=str(keyFile))
    context.load_verify_locations(cafile=str(caFile))
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def BuildServerContext(caFile: Path, certFile: Path, keyFile: Path) -> ssl.SSLContext:
    context = _NewContext(ssl.PROTOCOL_TLS_SERVER, caFile, certFile, keyFile)
    context.options |= ssl.OP_CIPHER_SERVER_PREFERENCE
    return context


def BuildClientContext(caFile: Path, certFile: Path, keyFile: Path) -> ssl.SSLContext:
    context = _NewContext(ssl.PROTOCOL_TLS_CLIENT, caFile, certFile, keyFile)
    context.check_hostname = True
    return context


def ServerContextFor(config: ClusterConfig, node: str) -> ssl.SSLContext:
    return BuildServerContext(config.CaFile(), config.CertFile(node), config.KeyFile(node))


def ClientContextFor(config: ClusterConfig, node: str) -> ssl.SSLContext:
    return BuildClientContext(config.CaFile(), config.CertFile(node), config.KeyFile(node))


def PeerNodeName(sslObject: ssl.SSLObject | ssl.SSLSocket) -> str:
    certificate = sslObject.getpeercert()
    if not certificate:
        raise PeerIdentityError("the peer presented no verified certificate")
    names = [value for kind, value in certificate.get("subjectAltName", ()) if kind == "DNS"]
    if len(names) != 1 or not NODE_NAME_PATTERN.match(names[0]):
        raise PeerIdentityError(f"the peer certificate must carry exactly one node name in its DNS SAN, found {names}")
    return names[0]


def DescribeSession(sslObject: ssl.SSLObject | ssl.SSLSocket) -> str:
    cipher = sslObject.cipher()
    return f"{sslObject.version()} {cipher[0] if cipher else 'unknown cipher'}"
