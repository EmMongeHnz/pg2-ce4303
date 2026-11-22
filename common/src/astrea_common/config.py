"""Static cluster configuration read from `config.toml` (same file on every machine).

The hub node (the host PC) listens; every other node dials it. Relative paths are resolved against the
directory of the configuration file. `cert-file` and `key-file` are templates where `{node}` becomes the
name of the node running on that machine, so each machine only needs its own key.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrea_common.messages import NODE_NAME_PATTERN, Role

MAX_PORT = 65535


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class NetworkSettings:
    heartbeatIntervalS: float = 2.0
    heartbeatTimeoutS: float = 6.0
    connectTimeoutS: float = 5.0
    reconnectInitialS: float = 0.5
    reconnectMaxS: float = 5.0


@dataclass(frozen=True)
class TlsSettings:
    caFile: Path
    certFileTemplate: str
    keyFileTemplate: str


@dataclass(frozen=True)
class NodeConfig:
    name: str
    roles: frozenset[Role]


@dataclass(frozen=True)
class HubConfig:
    node: str
    address: str
    bindAddress: str
    port: int


@dataclass(frozen=True)
class ClusterConfig:
    hub: HubConfig
    nodes: dict[str, NodeConfig]
    network: NetworkSettings
    tls: TlsSettings
    baseDir: Path

    def Node(self, name: str) -> NodeConfig:
        if name not in self.nodes:
            raise ConfigError(f"node '{name}' is not in the configuration")
        return self.nodes[name]

    def CertFile(self, node: str) -> Path:
        return self._Resolve(self.tls.certFileTemplate.format(node=node))

    def KeyFile(self, node: str) -> Path:
        return self._Resolve(self.tls.keyFileTemplate.format(node=node))

    def CaFile(self) -> Path:
        return self._Resolve(str(self.tls.caFile))

    def _Resolve(self, path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self.baseDir / candidate


def _Section(table: dict[str, Any], key: str) -> dict[str, Any]:
    section = table.get(key)
    if not isinstance(section, dict):
        raise ConfigError(f"missing section [{key}]")
    return section


def _RejectUnknownKeys(section: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(section) - allowed
    if unknown:
        raise ConfigError(f"unknown key(s) in {where}: {', '.join(sorted(unknown))}")


def _String(section: dict[str, Any], key: str, where: str, default: str | None = None) -> str:
    value = section.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}.{key} must be a non-empty string")
    return value


def _PositiveNumber(section: dict[str, Any], key: str, where: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"{where}.{key} must be a positive number")
    return float(value)


def _ParseNetwork(table: dict[str, Any]) -> NetworkSettings:
    section = table.get("network", {})
    if not isinstance(section, dict):
        raise ConfigError("[network] must be a table")
    _RejectUnknownKeys(section, {"heartbeat-interval-s", "heartbeat-timeout-s", "connect-timeout-s", "reconnect-initial-s", "reconnect-max-s"}, "[network]")
    defaults = NetworkSettings()
    settings = NetworkSettings(
        _PositiveNumber(section, "heartbeat-interval-s", "network", defaults.heartbeatIntervalS),
        _PositiveNumber(section, "heartbeat-timeout-s", "network", defaults.heartbeatTimeoutS),
        _PositiveNumber(section, "connect-timeout-s", "network", defaults.connectTimeoutS),
        _PositiveNumber(section, "reconnect-initial-s", "network", defaults.reconnectInitialS),
        _PositiveNumber(section, "reconnect-max-s", "network", defaults.reconnectMaxS),
    )
    if settings.heartbeatTimeoutS <= settings.heartbeatIntervalS:
        raise ConfigError("network.heartbeat-timeout-s must be larger than heartbeat-interval-s")
    if settings.reconnectMaxS < settings.reconnectInitialS:
        raise ConfigError("network.reconnect-max-s must not be smaller than reconnect-initial-s")
    return settings


def _ParseTls(table: dict[str, Any]) -> TlsSettings:
    section = _Section(table, "tls")
    _RejectUnknownKeys(section, {"ca-file", "cert-file", "key-file"}, "[tls]")
    return TlsSettings(Path(_String(section, "ca-file", "tls")), _String(section, "cert-file", "tls"), _String(section, "key-file", "tls"))


def _ParseHub(table: dict[str, Any]) -> HubConfig:
    section = _Section(table, "hub")
    _RejectUnknownKeys(section, {"node", "address", "bind-address", "port"}, "[hub]")
    port = section.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= MAX_PORT:
        raise ConfigError(f"hub.port must be an integer between 1 and {MAX_PORT}")
    address = _String(section, "address", "hub")
    return HubConfig(_String(section, "node", "hub"), address, _String(section, "bind-address", "hub", default=address), port)


def _ParseNodes(table: dict[str, Any]) -> dict[str, NodeConfig]:
    section = _Section(table, "nodes")
    nodes: dict[str, NodeConfig] = {}
    for name, entry in section.items():
        if not NODE_NAME_PATTERN.match(name):
            raise ConfigError(f"invalid node name '{name}'")
        if not isinstance(entry, dict):
            raise ConfigError(f"[nodes.{name}] must be a table")
        _RejectUnknownKeys(entry, {"roles"}, f"[nodes.{name}]")
        nodes[name] = NodeConfig(name, _ParseRoles(entry.get("roles"), name))
    if not nodes:
        raise ConfigError("[nodes] must list at least one node")
    return nodes


def _ParseRoles(value: Any, node: str) -> frozenset[Role]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"nodes.{node}.roles must be a non-empty list")
    roles = set()
    for item in value:
        try:
            roles.add(Role(item))
        except ValueError as error:
            raise ConfigError(f"nodes.{node}.roles has unknown role '{item}'") from error
    return frozenset(roles)


def ParseConfig(table: dict[str, Any], baseDir: Path) -> ClusterConfig:
    _RejectUnknownKeys(table, {"hub", "nodes", "network", "tls"}, "the top level")
    config = ClusterConfig(_ParseHub(table), _ParseNodes(table), _ParseNetwork(table), _ParseTls(table), baseDir)
    hubNode = config.nodes.get(config.hub.node)
    if hubNode is None or Role.COORDINATOR not in hubNode.roles:
        raise ConfigError(f"hub node '{config.hub.node}' must be listed in [nodes] with the coordinator role")
    others = [node.name for node in config.nodes.values() if Role.COORDINATOR in node.roles and node.name != config.hub.node]
    if others:
        raise ConfigError(f"only the hub node can have the coordinator role, also found on {', '.join(others)}")
    return config


def LoadConfig(path: str | Path) -> ClusterConfig:
    configPath = Path(path).resolve()
    try:
        with configPath.open("rb") as handle:
            table = tomllib.load(handle)
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file {configPath} does not exist") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{configPath} is not valid TOML: {error}") from error
    return ParseConfig(table, configPath.parent)
