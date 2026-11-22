"""TLS contexts: TLS 1.3 only, mutual authentication, identity checks and the ChaCha20 policy."""

import asyncio
import os
import ssl
import subprocess
import sys
from pathlib import Path

import pytest

from astrea_common.tls import (
    BuildClientContext,
    BuildServerContext,
    DescribeSession,
    PeerIdentityError,
    PeerNodeName,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CHACHA_CONF = REPO_ROOT / "pki" / "openssl-chacha20.cnf"
HOST_NAME = "host"


def ServerContext(pki, node: str = HOST_NAME) -> ssl.SSLContext:
    return BuildServerContext(pki.ca, pki.Cert(node), pki.Key(node))


def ClientContext(pki, node: str) -> ssl.SSLContext:
    return BuildClientContext(pki.ca, pki.Cert(node), pki.Key(node))


async def Handshake(serverContext, clientContext, serverHostname: str = HOST_NAME, tweakClient=None):
    """Runs one connection; returns (serverSideInfo or None, clientOutcome)."""
    serverInfo: list = []

    async def handle(reader, writer):
        sslObject = writer.get_extra_info("ssl_object")
        serverInfo.append((PeerNodeName(sslObject), DescribeSession(sslObject)))
        writer.write(b"ok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=serverContext)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=clientContext, server_hostname=serverHostname)
        try:
            data = await asyncio.wait_for(reader.read(2), 5)
            outcome = ("data", data, DescribeSession(writer.get_extra_info("ssl_object")), PeerNodeName(writer.get_extra_info("ssl_object")))
        finally:
            writer.close()
    except (ssl.SSLError, ConnectionError, OSError) as error:
        outcome = ("error", error)
    await asyncio.sleep(0.05)
    server.close()
    await server.wait_closed()
    return (serverInfo[0] if serverInfo else None), outcome


def test_mutual_handshake_uses_tls13_and_identifies_both_peers(pki):
    serverInfo, outcome = asyncio.run(Handshake(ServerContext(pki), ClientContext(pki, "worker-b")))
    assert serverInfo is not None and serverInfo[0] == "worker-b"
    assert serverInfo[1].startswith("TLSv1.3")
    assert outcome[0] == "data" and outcome[1] == b"ok"
    assert outcome[3] == "host"


def test_client_without_certificate_is_rejected(pki):
    bare = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    bare.minimum_version = ssl.TLSVersion.TLSv1_3
    bare.load_verify_locations(cafile=str(pki.ca))
    serverInfo, outcome = asyncio.run(Handshake(ServerContext(pki), bare))
    assert serverInfo is None
    assert outcome[0] == "error" or outcome[1] == b""


def test_client_certificate_from_another_ca_is_rejected(pki, foreignPki):
    context = BuildClientContext(pki.ca, foreignPki.Cert("pi"), foreignPki.Key("pi"))
    serverInfo, outcome = asyncio.run(Handshake(ServerContext(pki), context))
    assert serverInfo is None
    assert outcome[0] == "error" or outcome[1] == b""


def test_client_refuses_a_server_certificate_from_another_ca(pki, foreignPki):
    serverInfo, outcome = asyncio.run(Handshake(ServerContext(foreignPki), ClientContext(pki, "pi")))
    assert serverInfo is None
    assert outcome[0] == "error" and isinstance(outcome[1], ssl.SSLCertVerificationError)


def test_client_refuses_a_server_presenting_another_node_name(pki):
    serverInfo, outcome = asyncio.run(Handshake(ServerContext(pki, "worker-b"), ClientContext(pki, "pi")))
    assert serverInfo is None
    assert outcome[0] == "error" and isinstance(outcome[1], ssl.SSLCertVerificationError)


def test_client_limited_to_tls12_is_rejected(pki):
    context = ClientContext(pki, "pi")
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    serverInfo, outcome = asyncio.run(Handshake(ServerContext(pki), context))
    assert serverInfo is None and outcome[0] == "error"


def test_peer_without_a_certificate_has_no_identity():
    class NoCertificate:
        def getpeercert(self):
            return None

    with pytest.raises(PeerIdentityError):
        PeerNodeName(NoCertificate())


def test_certificate_with_two_dns_names_is_not_a_valid_identity():
    class TwoNames:
        def getpeercert(self):
            return {"subjectAltName": (("DNS", "host"), ("DNS", "pi"))}

    with pytest.raises(PeerIdentityError):
        PeerNodeName(TwoNames())


def NegotiatedCipherInSubprocess(pki, env: dict[str, str]) -> str:
    script = (
        "import asyncio, ssl, sys\n"
        "from pathlib import Path\n"
        "from astrea_common.tls import BuildServerContext, BuildClientContext\n"
        "d = Path(sys.argv[1])\n"
        "async def main():\n"
        "    async def handle(r, w):\n"
        "        w.close()\n"
        "    server = await asyncio.start_server(handle, '127.0.0.1', 0, ssl=BuildServerContext(d/'ca.crt', d/'host.crt', d/'host.key'))\n"
        "    port = server.sockets[0].getsockname()[1]\n"
        "    r, w = await asyncio.open_connection('127.0.0.1', port, ssl=BuildClientContext(d/'ca.crt', d/'pi.crt', d/'pi.key'), server_hostname='host')\n"
        "    print(w.get_extra_info('ssl_object').cipher()[0])\n"
        "    w.close(); server.close()\n"
        "asyncio.run(main())\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", script, str(pki.directory)], capture_output=True, text=True, env={**{k: v for k, v in os.environ.items() if k != "OPENSSL_CONF"}, **env})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_chacha20_config_restricts_the_process_to_chacha20_poly1305(pki):
    cipher = NegotiatedCipherInSubprocess(pki, {"OPENSSL_CONF": str(CHACHA_CONF)})
    assert cipher == "TLS_CHACHA20_POLY1305_SHA256"


def test_default_negotiation_is_still_an_aead_tls13_suite(pki):
    cipher = NegotiatedCipherInSubprocess(pki, {})
    assert cipher in {"TLS_AES_256_GCM_SHA384", "TLS_AES_128_GCM_SHA256", "TLS_CHACHA20_POLY1305_SHA256"}
