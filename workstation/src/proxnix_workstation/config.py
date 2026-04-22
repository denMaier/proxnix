from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from .errors import ConfigError


_ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def default_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "proxnix" / "config"


def _expand_home_string(value: str, home: Path) -> str:
    if value == "~":
        return str(home)
    if value.startswith("~/"):
        return str(home / value[2:])
    return value


def _parse_shell_value(raw_value: str, *, line_number: int) -> str:
    if raw_value == "":
        return ""
    try:
        parts = shlex.split(raw_value, comments=False, posix=True)
    except ValueError as exc:
        raise ConfigError(f"invalid shell quoting on line {line_number}") from exc
    if len(parts) != 1:
        raise ConfigError(
            f"config assignments must resolve to a single value on line {line_number}"
        )
    return parts[0]


def _parse_config_lines(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT_RE.match(line)
        if match is None:
            raise ConfigError(f"unsupported config line {line_number}: {raw_line}")
        key, raw_value = match.groups()
        values[key] = _parse_shell_value(raw_value, line_number=line_number)
    return values


@dataclass(frozen=True)
class WorkstationConfig:
    config_file: Path
    site_dir: Path | None
    hosts: tuple[str, ...]
    ssh_identity: Path | None
    remote_dir: PurePosixPath
    remote_priv_dir: PurePosixPath
    remote_host_relay_identity: PurePosixPath
    secret_provider: str = "embedded-sops"
    secret_provider_command: str | None = None
    scripts_dir: Path | None = None
    provider_environment: tuple[tuple[str, str], ...] = ()

    def to_json(self) -> str:
        payload = asdict(self)
        payload["config_file"] = str(self.config_file)
        payload["site_dir"] = None if self.site_dir is None else str(self.site_dir)
        payload["hosts"] = list(self.hosts)
        payload["ssh_identity"] = None if self.ssh_identity is None else str(self.ssh_identity)
        payload["remote_dir"] = str(self.remote_dir)
        payload["remote_priv_dir"] = str(self.remote_priv_dir)
        payload["remote_host_relay_identity"] = str(self.remote_host_relay_identity)
        payload["secret_provider"] = self.secret_provider
        payload["secret_provider_command"] = self.secret_provider_command
        payload["scripts_dir"] = None if self.scripts_dir is None else str(self.scripts_dir)
        payload["provider_environment"] = dict(self.provider_environment)
        return json.dumps(payload, indent=2, sort_keys=True)

    def provider_environment_map(self) -> dict[str, str]:
        return dict(self.provider_environment)

    def require_site_dir(self) -> Path:
        if self.site_dir is None:
            raise ConfigError(f"PROXNIX_SITE_DIR not set in {self.config_file}")
        if not self.site_dir.is_dir():
            raise ConfigError(f"site repo directory not found: {self.site_dir}")
        return self.site_dir


def load_workstation_config(
    config_file: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> WorkstationConfig:
    env = dict(os.environ if environ is None else environ)
    config_path = default_config_path() if config_file is None else Path(config_file).expanduser()
    home = Path(env.get("HOME", str(Path.home()))).expanduser()
    config_values: dict[str, str] = {}
    if config_path.is_file():
        config_values = _parse_config_lines(config_path.read_text(encoding="utf-8"))

    core_keys = {
        "PROXNIX_SITE_DIR",
        "PROXNIX_HOSTS",
        "PROXNIX_SSH_IDENTITY",
        "PROXNIX_REMOTE_DIR",
        "PROXNIX_REMOTE_PRIV_DIR",
        "PROXNIX_REMOTE_HOST_RELAY_IDENTITY",
        "PROXNIX_SECRET_PROVIDER",
        "PROXNIX_SECRET_PROVIDER_COMMAND",
        "PROXNIX_SCRIPTS_DIR",
    }

    raw_values = {
        "PROXNIX_SITE_DIR": env.get("PROXNIX_SITE_DIR", ""),
        "PROXNIX_HOSTS": env.get("PROXNIX_HOSTS", ""),
        "PROXNIX_SSH_IDENTITY": env.get("PROXNIX_SSH_IDENTITY", ""),
        "PROXNIX_REMOTE_DIR": env.get("PROXNIX_REMOTE_DIR", "/var/lib/proxnix"),
        "PROXNIX_REMOTE_PRIV_DIR": env.get("PROXNIX_REMOTE_PRIV_DIR", "/var/lib/proxnix/private"),
        "PROXNIX_REMOTE_HOST_RELAY_IDENTITY": env.get(
            "PROXNIX_REMOTE_HOST_RELAY_IDENTITY", "/etc/proxnix/host_relay_identity"
        ),
        "PROXNIX_SECRET_PROVIDER": env.get("PROXNIX_SECRET_PROVIDER", "embedded-sops"),
        "PROXNIX_SECRET_PROVIDER_COMMAND": env.get("PROXNIX_SECRET_PROVIDER_COMMAND", ""),
        "PROXNIX_SCRIPTS_DIR": env.get("PROXNIX_SCRIPTS_DIR", ""),
    }

    raw_values.update(config_values)

    site_dir_raw = _expand_home_string(raw_values["PROXNIX_SITE_DIR"], home).strip()
    ssh_identity_raw = _expand_home_string(raw_values["PROXNIX_SSH_IDENTITY"], home).strip()
    scripts_dir_raw = _expand_home_string(raw_values["PROXNIX_SCRIPTS_DIR"], home).strip()

    remote_dir = PurePosixPath(_expand_home_string(raw_values["PROXNIX_REMOTE_DIR"].strip(), home))
    remote_priv_dir = PurePosixPath(
        _expand_home_string(raw_values["PROXNIX_REMOTE_PRIV_DIR"].strip(), home)
    )
    remote_host_relay_identity = PurePosixPath(
        _expand_home_string(raw_values["PROXNIX_REMOTE_HOST_RELAY_IDENTITY"].strip(), home)
    )

    hosts_value = raw_values["PROXNIX_HOSTS"].strip()
    provider_environment: dict[str, str] = {
        key: value
        for key, value in env.items()
        if key.startswith("PROXNIX_") and key not in core_keys and value != ""
    }
    provider_environment.update(
        {
            key: _expand_home_string(value, home)
            for key, value in config_values.items()
            if key not in core_keys
        }
    )
    provider_environment.setdefault("PROXNIX_SOPS_MASTER_IDENTITY", str(home / ".ssh/id_ed25519"))

    return WorkstationConfig(
        config_file=config_path,
        site_dir=Path(site_dir_raw) if site_dir_raw else None,
        hosts=tuple(shlex.split(hosts_value)) if hosts_value else (),
        ssh_identity=Path(ssh_identity_raw) if ssh_identity_raw else None,
        remote_dir=remote_dir,
        remote_priv_dir=remote_priv_dir,
        remote_host_relay_identity=remote_host_relay_identity,
        secret_provider=raw_values["PROXNIX_SECRET_PROVIDER"].strip() or "embedded-sops",
        secret_provider_command=raw_values["PROXNIX_SECRET_PROVIDER_COMMAND"].strip() or None,
        scripts_dir=Path(scripts_dir_raw) if scripts_dir_raw else None,
        provider_environment=tuple(sorted(provider_environment.items())),
    )
