#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path


ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
SECRET_GROUP_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

KNOWN_KEYS = (
    "PROXNIX_SITE_DIR",
    "PROXNIX_SOPS_MASTER_IDENTITY",
    "PROXNIX_MASTER_IDENTITY",
    "PROXNIX_HOSTS",
    "PROXNIX_SSH_IDENTITY",
    "PROXNIX_REMOTE_DIR",
    "PROXNIX_REMOTE_PRIV_DIR",
    "PROXNIX_REMOTE_HOST_RELAY_IDENTITY",
    "PROXNIX_SECRET_PROVIDER",
    "PROXNIX_SECRET_PROVIDER_COMMAND",
    "PROXNIX_SCRIPTS_DIR",
)

DEFAULT_CONFIG = {
    "siteDir": "",
    "sopsMasterIdentity": "~/.ssh/id_ed25519",
    "hosts": "",
    "sshIdentity": "",
    "remoteDir": "/var/lib/proxnix",
    "remotePrivDir": "/var/lib/proxnix/private",
    "remoteHostRelayIdentity": "/etc/proxnix/host_relay_identity",
    "secretProvider": "embedded-sops",
    "secretProviderCommand": "",
    "scriptsDir": "",
}


def default_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "proxnix" / "config"


def _expand_home_string(value: str, home: Path) -> str:
    if value == "~":
        return str(home)
    if value.startswith("~/"):
        return str(home / value[2:])
    return value


def _parse_shell_value(raw_value: str, line_number: int) -> str:
    if raw_value == "":
        return ""
    try:
        parts = shlex.split(raw_value, comments=False, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid shell quoting on line {line_number}") from exc
    if len(parts) != 1:
        raise ValueError(
            f"config assignments must resolve to a single value on line {line_number}"
        )
    return parts[0]


def parse_config_lines(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = ASSIGNMENT_RE.match(line)
        if match is None:
            raise ValueError(f"unsupported config line {line_number}: {raw_line}")
        key, raw_value = match.groups()
        values[key] = _parse_shell_value(raw_value, line_number)
    return values


def shell_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def trim_blank_edges(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def preserved_config_lines(config_path: Path) -> list[str]:
    if not config_path.is_file():
        return []

    preserved: list[str] = []
    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        match = ASSIGNMENT_RE.match(stripped) if stripped else None
        if match is not None and match.group(1) in KNOWN_KEYS:
            continue
        preserved.append(raw_line)

    return trim_blank_edges(preserved)


def read_config_payload() -> tuple[dict[str, str], list[str], Path]:
    config_path = default_config_path()
    home = Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    config_values: dict[str, str] = {}
    preserved_keys: list[str] = []

    if config_path.is_file():
        config_values = parse_config_lines(config_path.read_text(encoding="utf-8"))
        preserved_keys = sorted(
            key for key in config_values if key.startswith("PROXNIX_") and key not in KNOWN_KEYS
        )

    def value_for(*names: str, default: str = "") -> str:
        for name in names:
            if name in config_values:
                return config_values[name]
            if name in os.environ and os.environ[name] != "":
                return os.environ[name]
        return default

    payload = {
        "siteDir": _expand_home_string(value_for("PROXNIX_SITE_DIR"), home).strip(),
        "sopsMasterIdentity": _expand_home_string(
            value_for(
                "PROXNIX_SOPS_MASTER_IDENTITY",
                "PROXNIX_MASTER_IDENTITY",
                default=str(home / ".ssh" / "id_ed25519"),
            ),
            home,
        ).strip(),
        "hosts": value_for("PROXNIX_HOSTS").strip(),
        "sshIdentity": _expand_home_string(value_for("PROXNIX_SSH_IDENTITY"), home).strip(),
        "remoteDir": value_for("PROXNIX_REMOTE_DIR", default="/var/lib/proxnix").strip(),
        "remotePrivDir": value_for(
            "PROXNIX_REMOTE_PRIV_DIR", default="/var/lib/proxnix/private"
        ).strip(),
        "remoteHostRelayIdentity": value_for(
            "PROXNIX_REMOTE_HOST_RELAY_IDENTITY", default="/etc/proxnix/host_relay_identity"
        ).strip(),
        "secretProvider": value_for("PROXNIX_SECRET_PROVIDER", default="embedded-sops").strip()
        or "embedded-sops",
        "secretProviderCommand": value_for("PROXNIX_SECRET_PROVIDER_COMMAND").strip(),
        "scriptsDir": _expand_home_string(value_for("PROXNIX_SCRIPTS_DIR"), home).strip(),
    }

    return payload, preserved_keys, config_path


def valid_secret_group_name(value: str) -> bool:
    return bool(value) and SECRET_GROUP_RE.fullmatch(value) is not None


def read_container_secret_groups(secret_groups_file: Path) -> list[str]:
    if not secret_groups_file.is_file():
        return []

    groups: list[str] = []
    seen: set[str] = set()
    for raw_line in secret_groups_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if not valid_secret_group_name(line):
            raise ValueError(f"invalid secret group name in {secret_groups_file}: {line}")
        if line not in seen:
            seen.add(line)
            groups.append(line)
    return groups


def scan_state(config: dict[str, str]) -> tuple[bool, list[dict[str, object]], list[str], list[str], list[str]]:
    site_dir_raw = config["siteDir"]
    warnings: list[str] = []
    containers: list[dict[str, object]] = []
    defined_groups: list[str] = []
    attached_groups: list[str] = []

    if not site_dir_raw:
        warnings.append("Set PROXNIX_SITE_DIR to scan your site repo.")
        return False, containers, defined_groups, attached_groups, warnings

    site_dir = Path(site_dir_raw).expanduser()
    if not site_dir.exists():
        warnings.append(f"Site directory does not exist: {site_dir}")
        return False, containers, defined_groups, attached_groups, warnings
    if not site_dir.is_dir():
        warnings.append(f"Site path is not a directory: {site_dir}")
        return False, containers, defined_groups, attached_groups, warnings

    containers_dir = site_dir / "containers"
    private_dir = site_dir / "private"
    private_containers_dir = private_dir / "containers"

    vmids: set[str] = set()
    for base in (containers_dir, private_containers_dir):
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if entry.is_dir() and entry.name.isdigit():
                vmids.add(entry.name)

    attached_group_names: set[str] = set()
    for vmid in sorted(vmids, key=int):
        public_dir = containers_dir / vmid
        private_container_dir = private_containers_dir / vmid
        dropins_dir = public_dir / "dropins"
        dropins = sorted(
            entry.name for entry in dropins_dir.iterdir()
        ) if dropins_dir.is_dir() else []

        try:
            secret_groups = read_container_secret_groups(public_dir / "secret-groups.list")
        except ValueError as exc:
            secret_groups = []
            warnings.append(str(exc))

        attached_group_names.update(secret_groups)

        containers.append(
            {
                "vmid": vmid,
                "containerPath": str(public_dir),
                "privateContainerPath": str(private_container_dir),
                "dropins": dropins,
                "hasConfig": public_dir.is_dir(),
                "hasSecretStore": (private_container_dir / "secrets.sops.yaml").is_file(),
                "hasIdentity": (private_container_dir / "age_identity.sops.yaml").is_file(),
                "secretGroups": secret_groups,
            }
        )

    groups_dir = private_dir / "groups"
    if groups_dir.is_dir():
        defined_groups = sorted(
            entry.name
            for entry in groups_dir.iterdir()
            if entry.is_dir() and valid_secret_group_name(entry.name)
        )

    attached_groups = sorted(attached_group_names)
    return True, containers, defined_groups, attached_groups, warnings


def snapshot() -> dict[str, object]:
    config, preserved_keys, config_path = read_config_payload()
    site_dir_exists, containers, defined_groups, attached_groups, warnings = scan_state(config)

    return {
        "configPath": str(config_path),
        "configExists": config_path.is_file(),
        "siteDirExists": site_dir_exists,
        "preservedConfigKeys": preserved_keys,
        "warnings": warnings,
        "config": config,
        "containers": containers,
        "definedSecretGroups": defined_groups,
        "attachedSecretGroups": attached_groups,
    }


def save_config(payload: dict[str, object]) -> dict[str, object]:
    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        raise ValueError("save-config requires a config object")

    config = {**DEFAULT_CONFIG, **{str(key): str(value) for key, value in raw_config.items()}}
    config_path = default_config_path()
    preserved_lines = preserved_config_lines(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# proxnix workstation config"]

    def emit(key: str, value: str) -> None:
        value = value.strip()
        if value:
            lines.append(f"{key}={shell_single_quoted(value)}")

    emit("PROXNIX_SITE_DIR", config["siteDir"])
    emit("PROXNIX_SOPS_MASTER_IDENTITY", config["sopsMasterIdentity"])
    emit("PROXNIX_HOSTS", config["hosts"])
    emit("PROXNIX_SSH_IDENTITY", config["sshIdentity"])
    emit("PROXNIX_REMOTE_DIR", config["remoteDir"])
    emit("PROXNIX_REMOTE_PRIV_DIR", config["remotePrivDir"])
    emit("PROXNIX_REMOTE_HOST_RELAY_IDENTITY", config["remoteHostRelayIdentity"])
    emit("PROXNIX_SECRET_PROVIDER", config["secretProvider"])
    emit("PROXNIX_SECRET_PROVIDER_COMMAND", config["secretProviderCommand"])
    emit("PROXNIX_SCRIPTS_DIR", config["scriptsDir"])

    if preserved_lines:
        lines.append("")
        lines.extend(preserved_lines)

    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return snapshot()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(json.dumps({"ok": False, "error": "missing bridge command"}))
        return 1

    command = argv[1]

    try:
        if command == "snapshot":
            result = snapshot()
        elif command == "save-config":
            payload = json.load(sys.stdin)
            result = save_config(payload)
        else:
            raise ValueError(f"unsupported bridge command: {command}")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    print(json.dumps({"ok": True, "result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
