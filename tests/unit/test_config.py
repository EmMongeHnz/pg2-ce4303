"""config.toml parsing and validation, including the file shipped at the repository root."""

from pathlib import Path

import pytest

from astrea_common.config import ConfigError, LoadConfig, ParseConfig
from astrea_common.messages import Role

REPO_ROOT = Path(__file__).resolve().parents[2]


def ValidTable() -> dict:
    return {
        "hub": {"node": "host", "address": "192.168.1.10", "bind-address": "0.0.0.0", "port": 7400},
        "tls": {"ca-file": "pki/out/ca.crt", "cert-file": "pki/out/{node}.crt", "key-file": "pki/out/{node}.key"},
        "nodes": {"host": {"roles": ["coordinator", "worker"]}, "pi": {"roles": ["worker", "device-agent"]}},
    }


def test_shipped_config_is_valid_and_lists_the_three_nodes():
    config = LoadConfig(REPO_ROOT / "config.toml")
    assert set(config.nodes) == {"host", "worker-b", "pi"}
    assert config.hub.node == "host" and config.hub.port == 7400
    assert config.nodes["pi"].roles == {Role.WORKER, Role.DEVICE_AGENT}
    assert config.CertFile("pi") == REPO_ROOT / "pki" / "example" / "pi.crt"
    assert config.KeyFile("worker-b") == REPO_ROOT / "pki" / "example" / "worker-b.key"
    assert config.CaFile() == REPO_ROOT / "pki" / "example" / "ca.crt"


def test_defaults_apply_when_network_section_is_missing():
    config = ParseConfig(ValidTable(), Path("/base"))
    assert config.network.heartbeatIntervalS == 2.0
    assert config.hub.bindAddress == "0.0.0.0"


def test_bind_address_defaults_to_the_hub_address():
    table = ValidTable()
    del table["hub"]["bind-address"]
    assert ParseConfig(table, Path("/base")).hub.bindAddress == "192.168.1.10"


def test_relative_paths_resolve_against_the_config_directory():
    config = ParseConfig(ValidTable(), Path("/base"))
    assert config.CertFile("host") == Path("/base/pki/out/host.crt")


def test_unknown_node_lookup_fails():
    with pytest.raises(ConfigError):
        ParseConfig(ValidTable(), Path("/base")).Node("ghost")


def Mutated(mutate) -> dict:
    table = ValidTable()
    mutate(table)
    return table


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.pop("hub"),
        lambda t: t.pop("tls"),
        lambda t: t.pop("nodes"),
        lambda t: t["hub"].update(port=0),
        lambda t: t["hub"].update(port=70000),
        lambda t: t["hub"].update(port="7400"),
        lambda t: t["hub"].update(node="ghost"),
        lambda t: t["nodes"]["host"].update(roles=["worker"]),
        lambda t: t["nodes"]["pi"].update(roles=["coordinator"]),
        lambda t: t["nodes"]["pi"].update(roles=["admin"]),
        lambda t: t["nodes"]["pi"].update(roles=[]),
        lambda t: t["nodes"].update({"Bad Name": {"roles": ["worker"]}}),
        lambda t: t["nodes"]["pi"].update(address="1.2.3.4"),
        lambda t: t["tls"].pop("ca-file"),
        lambda t: t.update(network={"heartbeat-interval-s": 5.0, "heartbeat-timeout-s": 5.0}),
        lambda t: t.update(network={"heartbeat-interval-s": -1.0}),
        lambda t: t.update(network={"reconnect-initial-s": 5.0, "reconnect-max-s": 1.0}),
        lambda t: t.update(network={"hearbeat-interval-s": 2.0}),
        lambda t: t.update(extra={}),
    ],
)
def test_invalid_configurations_are_rejected(mutate):
    with pytest.raises(ConfigError):
        ParseConfig(Mutated(mutate), Path("/base"))


def test_missing_file_and_invalid_toml_are_reported(tmp_path: Path):
    with pytest.raises(ConfigError):
        LoadConfig(tmp_path / "missing.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("[hub\nnode =")
    with pytest.raises(ConfigError):
        LoadConfig(broken)
