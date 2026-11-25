"""What the C network daemon demands of the TLS connection, checked from outside with Python clients and nodes:
TLS 1.3 only, mutual authentication with the cluster CA, a single node name per certificate, and the cipher policy."""

import asyncio
import ssl
import subprocess
from pathlib import Path

import pytest

from astrea_common.config import ParseConfig
from astrea_common.framing import WriteFrame
from astrea_common.link import HubServer, NodeClient
from astrea_common.messages import PROTOCOL_VERSION, Hello, MessageType, Role
from test_secure_link import FreePort, MakeConfig, WaitUntil
from tls_testing import BuildClientContext

WAIT_S = 6.0


async def Connect(config, context: ssl.SSLContext):
    reader, writer = await asyncio.open_connection(config.hub.address, config.hub.port, ssl=context, server_hostname="host")
    return reader, writer


async def ServerDropsTheConnection(reader: asyncio.StreamReader, timeout: float = WAIT_S) -> bool:
    async def drain():
        try:
            while await reader.read(4096):
                pass
        except (ConnectionError, ssl.SSLError, OSError):
            pass

    await asyncio.wait_for(drain(), timeout)
    return True


def ClientContextWithoutCertificate(pki) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_verify_locations(str(pki.ca))
    return context


def test_a_client_without_a_certificate_is_rejected(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        hub = HubServer(config)
        await hub.Start()
        try:
            try:
                reader, writer = await Connect(config, ClientContextWithoutCertificate(pki))
                await ServerDropsTheConnection(reader)
                writer.close()
            except (ssl.SSLError, ConnectionError, OSError):
                pass
            await WaitUntil(lambda: hub.rejections, WAIT_S)
            return hub.rejections, hub.peers
        finally:
            await hub.Stop()

    rejections, peers = asyncio.run(scenario())
    assert rejections and not peers


def test_a_client_certificate_from_another_ca_is_rejected(pki, foreignPki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        hub = HubServer(config)
        await hub.Start()
        try:
            context = BuildClientContext(pki.ca, foreignPki.Cert("pi"), foreignPki.Key("pi"))
            try:
                reader, writer = await Connect(config, context)
                await WriteFrame(writer, int(MessageType.HELLO), Hello(PROTOCOL_VERSION, "pi", Role.WORKER).ToPayload())
                await ServerDropsTheConnection(reader)
                writer.close()
            except (ssl.SSLError, ConnectionError, OSError):
                pass
            await WaitUntil(lambda: hub.rejections, WAIT_S)
            return hub.peers
        finally:
            await hub.Stop()

    assert asyncio.run(scenario()) == {}


def test_a_client_limited_to_tls12_cannot_connect(pki):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        hub = HubServer(config)
        await hub.Start()
        try:
            context = BuildClientContext(pki.ca, pki.Cert("pi"), pki.Key("pi"))
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.maximum_version = ssl.TLSVersion.TLSv1_2
            with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
                reader, writer = await Connect(config, context)
                await ServerDropsTheConnection(reader)
            return hub.peers
        finally:
            await hub.Stop()

    assert asyncio.run(scenario()) == {}


def test_a_node_refuses_a_hub_whose_certificate_is_from_another_ca(pki, foreignPki):
    async def scenario():
        port = FreePort()
        hub = HubServer(MakeConfig(foreignPki, port, devicePort=FreePort()))
        await hub.Start()
        client = NodeClient(MakeConfig(pki, port, devicePort=hub.config.hub.devicePort), "worker-b", Role.WORKER)
        runner = asyncio.create_task(client.Run())
        try:
            with pytest.raises(asyncio.TimeoutError):
                await client.WaitConnected(1.5)
            return hub.peers
        finally:
            await client.Stop()
            await runner
            await hub.Stop()

    assert asyncio.run(scenario()) == {}


def test_a_node_refuses_a_hub_that_presents_another_node_name(pki):
    async def scenario():
        port = FreePort()
        table = {
            "hub": {"node": "host", "address": "127.0.0.1", "port": port},
            "tls": {"ca-file": str(pki.ca), "cert-file": str(pki.Cert("worker-b")), "key-file": str(pki.Key("worker-b"))},
            "nodes": {"host": {"roles": ["coordinator"]}, "worker-b": {"roles": ["worker"]}},
        }
        hub = HubServer(ParseConfig(table, Path("/unused")))
        await hub.Start()
        client = NodeClient(MakeConfig(pki, port), "worker-b", Role.WORKER)
        runner = asyncio.create_task(client.Run())
        try:
            with pytest.raises(asyncio.TimeoutError):
                await client.WaitConnected(1.5)
            return hub.peers
        finally:
            await client.Stop()
            await runner
            await hub.Stop()

    assert asyncio.run(scenario()) == {}


def IssueCertificateWithTwoNames(pki, directory: Path) -> tuple[Path, Path]:
    key, request, certificate, extensions = (directory / name for name in ("two.key", "two.csr", "two.crt", "two.ext"))
    extensions.write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth,clientAuth\nsubjectAltName=DNS:pi,DNS:worker-b\n")
    for command in (
        ["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", str(key)],
        ["openssl", "req", "-new", "-key", str(key), "-subj", "/CN=pi", "-out", str(request)],
        ["openssl", "x509", "-req", "-in", str(request), "-CA", str(pki.ca), "-CAkey", str(pki.directory / "ca.key"), "-CAcreateserial", "-days", "30", "-extfile", str(extensions), "-out", str(certificate)],
    ):
        subprocess.run(command, check=True, capture_output=True)
    return certificate, key


def test_a_certificate_naming_two_nodes_is_not_a_valid_identity(pki, tmp_path):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        certificate, key = IssueCertificateWithTwoNames(pki, tmp_path)
        hub = HubServer(config)
        await hub.Start()
        try:
            try:
                reader, writer = await Connect(config, BuildClientContext(pki.ca, certificate, key))
                await WriteFrame(writer, int(MessageType.HELLO), Hello(PROTOCOL_VERSION, "pi", Role.WORKER).ToPayload())
                await ServerDropsTheConnection(reader)
                writer.close()
            except (ssl.SSLError, ConnectionError, OSError):
                pass
            await WaitUntil(lambda: hub.rejections, WAIT_S)
            return hub.rejections, hub.peers
        finally:
            await hub.Stop()

    rejections, peers = asyncio.run(scenario())
    assert any("no node name" in reason for reason in rejections) and not peers


@pytest.mark.parametrize("preferChacha, expected", [(False, "TLS_AES_256_GCM_SHA384"), (True, "TLS_CHACHA20_POLY1305_SHA256")])
def test_the_cipher_follows_the_preference_of_the_node(pki, preferChacha, expected):
    async def scenario():
        config = MakeConfig(pki, FreePort())
        hub = HubServer(config)
        await hub.Start()
        client = NodeClient(config, "worker-b", Role.WORKER, preferChacha=preferChacha)
        runner = asyncio.create_task(client.Run())
        try:
            connection = await client.WaitConnected(WAIT_S)
            return connection.peer.session
        finally:
            await client.Stop()
            await runner
            await hub.Stop()

    session = asyncio.run(scenario())
    assert session == f"TLSv1.3 {expected}"
