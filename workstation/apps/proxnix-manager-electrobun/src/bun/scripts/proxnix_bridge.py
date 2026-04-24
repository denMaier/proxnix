#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import glob
import re
import shlex
import subprocess
import sys
from pathlib import Path


ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
SECRET_GROUP_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
DOCTOR_HEADING_RE = re.compile(r"^\[(.+)\]$")
DOCTOR_LINE_RE = re.compile(r"^\s+(OK|WARN|FAIL|INFO)\s+(.+)$")

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
    "sopsMasterIdentity": "",
    "hosts": "",
    "sshIdentity": "",
    "remoteDir": "/var/lib/proxnix",
    "remotePrivDir": "/var/lib/proxnix/private",
    "remoteHostRelayIdentity": "/etc/proxnix/host_relay_identity",
    "secretProvider": "embedded-sops",
    "secretProviderCommand": "",
    "scriptsDir": "",
}

INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS = 60 * 60

_PYTHONPATH_BOOTSTRAPPED = False


def default_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "proxnix" / "config"


def _resource_dir() -> Path | None:
    script_path = Path(__file__).resolve()
    for parent in script_path.parents:
        if parent.name == "Resources" and parent.parent.name == "Contents":
            return parent
    return None


def _repo_root() -> Path | None:
    script_path = Path(__file__).resolve()
    for parent in script_path.parents:
        if (parent / "workstation" / "src" / "proxnix_workstation").is_dir():
            return parent
    return None


def _pythonpath_entries() -> list[str]:
    entries: list[str] = []

    resources_dir = _resource_dir()
    if resources_dir is not None:
        bundled_python = resources_dir / "lib" / "python"
        if (bundled_python / "proxnix_workstation").is_dir():
            entries.append(str(bundled_python))

    repo_root = _repo_root()
    if repo_root is not None:
        repo_python = repo_root / "workstation" / "src"
        if (repo_python / "proxnix_workstation").is_dir():
            entries.append(str(repo_python))
        venv_site_packages = _repo_venv_site_packages(repo_root)
        entries.extend(venv_site_packages)

    deduped: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry not in seen:
            seen.add(entry)
            deduped.append(entry)
    return deduped


def _ensure_pythonpath_bootstrap() -> None:
    global _PYTHONPATH_BOOTSTRAPPED

    if _PYTHONPATH_BOOTSTRAPPED:
        return

    for entry in reversed(_pythonpath_entries()):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    _PYTHONPATH_BOOTSTRAPPED = True


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    extra_entries = _pythonpath_entries()
    if extra_entries:
        existing = env.get("PYTHONPATH", "").strip()
        combined = extra_entries.copy()
        if existing:
            combined.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(combined)
    return env


def _command_output(stdout: str, stderr: str) -> str:
    parts = [part.strip() for part in (stdout, stderr) if part.strip()]
    return "\n".join(parts)


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    extra_paths = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    existing = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    merged: list[str] = []
    for path in [*extra_paths, *existing]:
        if path not in merged:
            merged.append(path)
    env["PATH"] = os.pathsep.join(merged)
    env["HOME"] = os.environ.get("HOME", str(Path.home()))
    return env


def _repo_workstation_dir() -> Path | None:
    repo_root = _repo_root()
    if repo_root is None:
        return None

    workstation_dir = repo_root / "workstation"
    if workstation_dir.is_dir():
        return workstation_dir
    return None


def _repo_venv_site_packages(repo_root: Path) -> list[str]:
    patterns = [
        repo_root / "workstation" / ".venv" / "lib" / "python*" / "site-packages",
        repo_root / "workstation" / ".venv" / "Lib" / "site-packages",
    ]

    paths: list[str] = []
    for pattern in patterns:
        for match in glob.glob(str(pattern)):
            if Path(match).is_dir():
                paths.append(match)
    return sorted(set(paths))


def _bundled_cli(script_name: str) -> Path | None:
    resources_dir = _resource_dir()
    if resources_dir is None:
        return None

    cli_path = resources_dir / "bin" / script_name
    if cli_path.is_file():
        return cli_path
    return None


def _repo_cli(script_name: str) -> Path | None:
    workstation_dir = _repo_workstation_dir()
    if workstation_dir is None:
        return None

    cli_path = workstation_dir / "bin" / script_name
    if cli_path.is_file():
        return cli_path
    return None


def _doctor_command() -> tuple[list[str], dict[str, str]]:
    bundled_cli = _bundled_cli("proxnix-doctor")
    if bundled_cli is not None:
        return [str(bundled_cli)], _subprocess_env()
    repo_cli = _repo_cli("proxnix-doctor")
    if repo_cli is not None:
        return [str(repo_cli)], _subprocess_env()
    return [sys.executable, "-m", "proxnix_workstation.doctor_cli"], _subprocess_env()


def _publish_command() -> tuple[list[str], dict[str, str]]:
    bundled_cli = _bundled_cli("proxnix-publish")
    if bundled_cli is not None:
        return [str(bundled_cli)], _subprocess_env()
    repo_cli = _repo_cli("proxnix-publish")
    if repo_cli is not None:
        return [str(repo_cli)], _subprocess_env()
    return [sys.executable, "-m", "proxnix_workstation.publish_cli"], _subprocess_env()


def sidebar_metadata_path() -> Path:
    return default_config_path().parent / "manager-sidebar-state.json"


def _expand_home_string(value: str, home: Path) -> str:
    if value == "~":
        return str(home)
    if value.startswith("~/"):
        return str(home / value[2:])
    return value


def _normalized_site_key(site_dir: str) -> str:
    return str(Path(site_dir).expanduser().resolve(strict=False))


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


def _normalize_sidebar_metadata(raw_value: object) -> dict[str, object]:
    metadata = raw_value if isinstance(raw_value, dict) else {}
    labels = metadata.get("labels")
    normalized_labels: list[str] = []
    seen: set[str] = set()

    if isinstance(labels, list):
        for label in labels:
            if not isinstance(label, str):
                continue
            trimmed = label.strip()
            if not trimmed:
                continue
            key = trimmed.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized_labels.append(trimmed)

    return {
        "displayName": str(metadata.get("displayName", "")).strip(),
        "group": str(metadata.get("group", "")).strip(),
        "labels": normalized_labels,
    }


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


def load_sidebar_state() -> dict[str, object]:
    metadata_path = sidebar_metadata_path()
    if not metadata_path.is_file():
        return {"sites": {}}

    try:
        raw_state = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"sites": {}}

    if not isinstance(raw_state, dict):
        return {"sites": {}}

    raw_sites = raw_state.get("sites")
    if not isinstance(raw_sites, dict):
        return {"sites": {}}

    normalized_sites: dict[str, object] = {}
    for site_key, raw_site_state in raw_sites.items():
        if not isinstance(site_key, str) or not isinstance(raw_site_state, dict):
            continue
        raw_containers = raw_site_state.get("containers")
        if not isinstance(raw_containers, dict):
            continue
        containers: dict[str, object] = {}
        for vmid, raw_metadata in raw_containers.items():
            if not isinstance(vmid, str):
                continue
            normalized = _normalize_sidebar_metadata(raw_metadata)
            if (
                normalized["displayName"]
                or normalized["group"]
                or normalized["labels"]
            ):
                containers[vmid] = normalized
        if containers:
            normalized_sites[site_key] = {"containers": containers}

    return {"sites": normalized_sites}


def read_sidebar_metadata(site_dir: str) -> dict[str, dict[str, object]]:
    if not site_dir:
        return {}

    state = load_sidebar_state()
    sites = state.get("sites")
    if not isinstance(sites, dict):
        return {}

    site_state = sites.get(_normalized_site_key(site_dir))
    if not isinstance(site_state, dict):
        return {}

    containers = site_state.get("containers")
    if not isinstance(containers, dict):
        return {}

    return {
        vmid: _normalize_sidebar_metadata(raw_metadata)
        for vmid, raw_metadata in containers.items()
        if isinstance(vmid, str)
    }


def save_sidebar_metadata(payload: dict[str, object]) -> dict[str, object]:
    vmid = str(payload.get("vmid", "")).strip()
    raw_metadata = payload.get("metadata")

    if not vmid:
        raise ValueError("save-sidebar-metadata requires a vmid")
    if not isinstance(raw_metadata, dict):
        raise ValueError("save-sidebar-metadata requires a metadata object")

    config, _preserved_keys, _config_path = read_config_payload()
    site_dir = config["siteDir"]
    if not site_dir:
        raise ValueError("set PROXNIX_SITE_DIR before saving sidebar metadata")

    state = load_sidebar_state()
    sites = state.setdefault("sites", {})
    if not isinstance(sites, dict):
        state["sites"] = {}
        sites = state["sites"]

    site_key = _normalized_site_key(site_dir)
    site_state = sites.get(site_key)
    if not isinstance(site_state, dict):
        site_state = {"containers": {}}
        sites[site_key] = site_state

    containers = site_state.get("containers")
    if not isinstance(containers, dict):
        containers = {}
        site_state["containers"] = containers

    normalized = _normalize_sidebar_metadata(raw_metadata)
    if normalized["displayName"] or normalized["group"] or normalized["labels"]:
        containers[vmid] = normalized
    else:
        containers.pop(vmid, None)

    if not containers:
        sites.pop(site_key, None)

    metadata_path = sidebar_metadata_path()
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return snapshot()


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


def _load_provider_context() -> tuple[tuple[object, object, object] | None, str | None]:
    """Try to load the workstation config and secret provider.

    Returns ((config, site_paths, provider), None) on success, or (None, error) on failure.
    """
    try:
        _ensure_pythonpath_bootstrap()
        from proxnix_workstation.config import load_workstation_config
        from proxnix_workstation.paths import SitePaths
        from proxnix_workstation.secret_provider import load_secret_provider

        config = load_workstation_config()
        site_paths = SitePaths.from_config(config)
        provider = load_secret_provider(config, site_paths)
        return (config, site_paths, provider), None
    except Exception as exc:
        return None, str(exc)


def _check_container_identity(ctx: tuple[object, object, object], vmid: str) -> bool:
    config, site_paths, provider = ctx
    try:
        from proxnix_workstation.provider_keys import have_container_private_key

        return have_container_private_key(config, provider, site_paths, vmid)
    except Exception:
        return False


def _check_defined_groups(
    ctx: tuple[object, object, object],
    attached_group_names: set[str],
    site_dir: Path,
) -> list[str]:
    config, site_paths, provider = ctx
    try:
        from proxnix_workstation.secret_provider_embedded import EmbeddedSopsProvider
        from proxnix_workstation.secret_provider_types import group_scope

        is_embedded = isinstance(provider, EmbeddedSopsProvider)
    except ImportError:
        return []

    defined: list[str] = []

    if is_embedded:
        # For embedded-sops: check if the group store file exists (fast, no decryption).
        # Also discover groups that have a directory but aren't attached yet.
        groups_dir = site_dir / "private" / "groups"
        if groups_dir.is_dir():
            all_group_names = {
                entry.name
                for entry in groups_dir.iterdir()
                if entry.is_dir() and valid_secret_group_name(entry.name)
            }
        else:
            all_group_names = set()
        defined = sorted(all_group_names | {
            g for g in attached_group_names
            if (site_dir / "private" / "groups" / g / "secrets.sops.yaml").is_file()
        })
    else:
        # For other providers: ask the provider which groups it knows about.
        for group in sorted(attached_group_names):
            try:
                if provider.has_any(group_scope(group)):
                    defined.append(group)
            except Exception:
                pass

    return defined


def _scan_local_defined_groups(site_dir: Path) -> list[str]:
    groups_dir = site_dir / "private" / "groups"
    if not groups_dir.is_dir():
        return []
    return sorted(
        entry.name
        for entry in groups_dir.iterdir()
        if entry.is_dir() and valid_secret_group_name(entry.name)
    )


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
                "hasIdentity": (private_container_dir / "age_identity.sops.yaml").is_file(),
                "secretGroups": secret_groups,
            }
        )

    defined_groups = _scan_local_defined_groups(site_dir)
    attached_groups = sorted(attached_group_names)
    return True, containers, defined_groups, attached_groups, warnings


def secrets_provider_status() -> dict[str, object]:
    config, _preserved_keys, _config_path = read_config_payload()
    site_dir_raw = config["siteDir"]
    warnings: list[str] = []
    container_identities: dict[str, bool] = {}
    defined_groups: list[str] = []

    if not site_dir_raw:
        return {
            "provider": config["secretProvider"],
            "definedSecretGroups": defined_groups,
            "containerIdentities": container_identities,
            "warnings": ["Set PROXNIX_SITE_DIR to scan your site repo."],
        }

    site_dir = Path(site_dir_raw).expanduser()
    if not site_dir.is_dir():
        return {
            "provider": config["secretProvider"],
            "definedSecretGroups": defined_groups,
            "containerIdentities": container_identities,
            "warnings": [f"Site path is not a directory: {site_dir}"],
        }

    _site_dir_exists, containers, _local_defined, attached_groups, scan_warnings = scan_state(config)
    warnings.extend(scan_warnings)

    ctx, provider_error = _load_provider_context()
    if provider_error:
        warnings.append(f"Secret backend unavailable: {provider_error}")
        return {
            "provider": config["secretProvider"],
            "definedSecretGroups": defined_groups,
            "containerIdentities": container_identities,
            "warnings": warnings,
        }

    assert ctx is not None
    for container in containers:
        vmid = str(container.get("vmid", "")).strip()
        if not vmid:
            continue
        container_identities[vmid] = _check_container_identity(ctx, vmid)

    defined_groups = _check_defined_groups(ctx, set(attached_groups), site_dir)

    return {
        "provider": config["secretProvider"],
        "definedSecretGroups": defined_groups,
        "containerIdentities": container_identities,
        "warnings": warnings,
    }


def snapshot() -> dict[str, object]:
    config, preserved_keys, config_path = read_config_payload()
    site_dir_exists, containers, defined_groups, attached_groups, warnings = scan_state(config)
    sidebar_metadata = read_sidebar_metadata(config["siteDir"])

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
        "sidebarMetadata": sidebar_metadata,
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


def _run_cli(
    args: list[str],
    *,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> tuple[str, str, int]:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return result.stdout, result.stderr, result.returncode


def _parse_doctor_output(output: str) -> dict[str, object]:
    sections: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for line in output.splitlines():
        heading_match = DOCTOR_HEADING_RE.match(line.strip())
        if heading_match:
            current = {"heading": heading_match.group(1), "entries": []}
            sections.append(current)
            continue
        entry_match = DOCTOR_LINE_RE.match(line)
        if entry_match and current is not None:
            entries = current["entries"]
            assert isinstance(entries, list)
            entries.append({
                "level": entry_match.group(1).lower(),
                "text": entry_match.group(2),
            })

    oks = sum(1 for s in sections for e in s["entries"] if isinstance(e, dict) and e.get("level") == "ok")  # type: ignore[union-attr]
    warns = sum(1 for s in sections for e in s["entries"] if isinstance(e, dict) and e.get("level") == "warn")  # type: ignore[union-attr]
    fails = sum(1 for s in sections for e in s["entries"] if isinstance(e, dict) and e.get("level") == "fail")  # type: ignore[union-attr]

    return {"sections": sections, "oks": oks, "warns": warns, "fails": fails}


def run_doctor(payload: object) -> dict[str, object]:
    config, _, _ = read_config_payload()
    site_dir = config["siteDir"]
    if not site_dir:
        return {"sections": [], "oks": 0, "warns": 0, "fails": 0, "exitCode": 1, "error": "Set site directory first."}

    args, env = _doctor_command()
    args.append("--site-only")
    opts = payload if isinstance(payload, dict) else {}
    if opts.get("configOnly"):
        args.append("--config-only")
    vmid = opts.get("vmid")
    if vmid:
        args.extend(["--vmid", str(vmid)])

    try:
        stdout, stderr, exit_code = _run_cli(
            args,
            timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "sections": [],
            "oks": 0,
            "warns": 0,
            "fails": 0,
            "exitCode": 1,
            "error": "Doctor check timed out after 60 minutes.",
        }
    except Exception as exc:
        return {"sections": [], "oks": 0, "warns": 0, "fails": 0, "exitCode": 1, "error": str(exc)}

    result = _parse_doctor_output(stdout)
    result["exitCode"] = exit_code
    combined_error = stderr.strip() or stdout.strip()
    if exit_code != 0 and combined_error:
        result["error"] = combined_error
    elif not result["sections"] and combined_error:
        result["error"] = combined_error
    return result


def run_publish(payload: object) -> dict[str, object]:
    config, _, _ = read_config_payload()
    site_dir = config["siteDir"]
    if not site_dir:
        return {"output": "", "exitCode": 1, "error": "Set site directory first."}

    args, env = _publish_command()
    opts = payload if isinstance(payload, dict) else {}
    if opts.get("dryRun"):
        args.extend(["--dry-run", "--report-changes"])
    if opts.get("configOnly"):
        args.append("--config-only")
    vmid = opts.get("vmid")
    if vmid:
        args.extend(["--vmid", str(vmid)])
    for host in opts.get("hosts") or []:
        args.append(str(host))

    try:
        stdout, stderr, exit_code = _run_cli(
            args,
            timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"output": "", "exitCode": 1, "error": "Publish timed out after 60 minutes."}
    except Exception as exc:
        return {"output": "", "exitCode": 1, "error": str(exc)}

    output = _command_output(stdout, stderr)
    return {
        "output": output,
        "exitCode": exit_code,
        "error": (
            stderr.strip()
            if stderr.strip()
            else (stdout.strip() if exit_code != 0 and stdout.strip() else "")
        ),
    }


def git_status(_payload: object) -> dict[str, object]:
    config, _, _ = read_config_payload()
    site_dir = config["siteDir"]
    empty: dict[str, object] = {
        "isRepo": False,
        "branch": "",
        "clean": True,
        "staged": [],
        "unstaged": [],
        "untracked": [],
        "files": [],
        "log": [],
        "ahead": 0,
        "behind": 0,
        "hasRemote": False,
        "upstream": "",
        "error": "",
    }
    if not site_dir:
        empty["error"] = "Set site directory first."
        return empty

    site_path = Path(site_dir).expanduser()
    if not site_path.is_dir():
        empty["error"] = f"Site directory not found: {site_dir}"
        return empty

    def git(*args: str) -> tuple[str, int]:
        try:
            result = subprocess.run(
                ["git", "-C", str(site_path), *args],
                capture_output=True, text=True, timeout=30, env=_git_env(),
            )
            return _command_output(result.stdout, result.stderr), result.returncode
        except Exception:
            return "", 1

    _, rc = git("rev-parse", "--is-inside-work-tree")
    if rc != 0:
        empty["error"] = "Site directory is not a git repository."
        return empty

    branch_out, _ = git("branch", "--show-current")
    status_out, _ = git("status", "--porcelain=v1", "-u")
    log_out, _ = git("log", "--oneline", "-15")
    upstream_out, upstream_rc = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")

    files: list[dict[str, str]] = []
    staged: list[dict[str, str]] = []
    unstaged: list[dict[str, str]] = []
    untracked: list[dict[str, str]] = []
    for line in status_out.splitlines():
        if len(line) >= 3:
            index_flag = line[0]
            worktree_flag = line[1]
            path = line[3:]
            status = line[:2].strip() or "?"
            entry = {"status": status, "path": path}
            files.append(entry)
            if index_flag == "?":
                untracked.append({"status": "?", "path": path})
            else:
                if index_flag != " ":
                    staged.append({"status": index_flag, "path": path})
                if worktree_flag != " ":
                    unstaged.append({"status": worktree_flag, "path": path})

    log_entries: list[dict[str, str]] = []
    for line in log_out.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            log_entries.append({"hash": parts[0], "message": parts[1]})

    ahead = 0
    behind = 0
    has_remote = upstream_rc == 0 and bool(upstream_out)
    if has_remote:
        count_out, count_rc = git("rev-list", "--left-right", "--count", f"HEAD...{upstream_out}")
        if count_rc == 0:
            counts = count_out.split()
            if len(counts) >= 2:
                try:
                    ahead = int(counts[0])
                    behind = int(counts[1])
                except ValueError:
                    ahead = 0
                    behind = 0

    return {
        "isRepo": True,
        "branch": branch_out,
        "clean": len(files) == 0,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "files": files,
        "log": log_entries,
        "ahead": ahead,
        "behind": behind,
        "hasRemote": has_remote,
        "upstream": upstream_out if has_remote else "",
        "error": "",
    }


def _git_site_path() -> tuple[Path | None, dict[str, object] | None]:
    config, _, _ = read_config_payload()
    site_dir = config["siteDir"]
    if not site_dir:
        return None, {"output": "", "exitCode": 1, "error": "Set site directory first."}

    site_path = Path(site_dir).expanduser()
    if not site_path.is_dir():
        return None, {"output": "", "exitCode": 1, "error": f"Site directory not found: {site_dir}"}

    return site_path, None


def _run_git(site_path: Path, *args: str, timeout: int = 120) -> tuple[str, int]:
    try:
        result = subprocess.run(
            ["git", "-C", str(site_path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_git_env(),
        )
        return _command_output(result.stdout, result.stderr), result.returncode
    except subprocess.TimeoutExpired:
        return f"git {' '.join(args)} timed out.", 124
    except Exception as exc:
        return str(exc), 1


def _command_result(output: str, exit_code: int, fallback_success: str = "") -> dict[str, object]:
    cleaned = output.strip() or (fallback_success if exit_code == 0 else "")
    return {
        "output": cleaned,
        "exitCode": exit_code,
        "error": "" if exit_code == 0 else cleaned,
    }


def _ensure_git_repo(site_path: Path) -> dict[str, object] | None:
    _, repo_rc = _run_git(site_path, "rev-parse", "--is-inside-work-tree")
    if repo_rc != 0:
        return {"output": "", "exitCode": 1, "error": "Site directory is not a git repository."}
    return None


def git_add(payload: object) -> dict[str, object]:
    site_path, error = _git_site_path()
    if error is not None:
        return error
    assert site_path is not None
    repo_error = _ensure_git_repo(site_path)
    if repo_error is not None:
        return repo_error

    opts = payload if isinstance(payload, dict) else {}
    if opts.get("all"):
        output, exit_code = _run_git(site_path, "add", "-A")
        return _command_result(output, exit_code, "All changes staged.")

    path = str(opts.get("file", "")).strip()
    if not path:
        return {"output": "", "exitCode": 1, "error": "Choose a file to add."}
    output, exit_code = _run_git(site_path, "add", "--", path)
    return _command_result(output, exit_code, f"Staged {path}.")


def git_commit(payload: object) -> dict[str, object]:
    site_path, error = _git_site_path()
    if error is not None:
        return error
    assert site_path is not None
    repo_error = _ensure_git_repo(site_path)
    if repo_error is not None:
        return repo_error

    opts = payload if isinstance(payload, dict) else {}
    message = str(opts.get("message", "")).strip()
    if not message:
        return {"output": "", "exitCode": 1, "error": "Commit message cannot be empty."}

    output, exit_code = _run_git(site_path, "commit", "-m", message)
    return _command_result(output, exit_code)


def git_push(_payload: object) -> dict[str, object]:
    site_path, error = _git_site_path()
    if error is not None:
        return error
    assert site_path is not None
    repo_error = _ensure_git_repo(site_path)
    if repo_error is not None:
        return repo_error

    output, exit_code = _run_git(site_path, "push", timeout=180)
    return _command_result(output, exit_code, "Pushed successfully.")


def open_in_editor(payload: object) -> dict[str, object]:
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", ""))
    if not editor:
        return {"opened": False, "error": "$EDITOR is not set. Export EDITOR in your shell profile."}

    opts = payload if isinstance(payload, dict) else {}
    path = str(opts.get("path", "")).strip()
    if not path:
        return {"opened": False, "error": "No path provided."}

    parts = shlex.split(editor)
    subprocess.Popen([*parts, path], start_new_session=True)
    return {"opened": True, "editor": parts[0]}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(json.dumps({"ok": False, "error": "missing bridge command"}))
        return 1

    command = argv[1]

    try:
        if command == "snapshot":
            result = snapshot()
        elif command == "secrets-provider-status":
            result = secrets_provider_status()
        elif command == "save-config":
            payload = json.load(sys.stdin)
            result = save_config(payload)
        elif command == "save-sidebar-metadata":
            payload = json.load(sys.stdin)
            result = save_sidebar_metadata(payload)
        elif command == "run-doctor":
            payload = json.load(sys.stdin)
            result = run_doctor(payload)
        elif command == "run-publish":
            payload = json.load(sys.stdin)
            result = run_publish(payload)
        elif command == "git-status":
            result = git_status(None)
        elif command == "git-add":
            payload = json.load(sys.stdin)
            result = git_add(payload)
        elif command == "git-commit":
            payload = json.load(sys.stdin)
            result = git_commit(payload)
        elif command == "git-push":
            result = git_push(None)
        elif command == "open-in-editor":
            payload = json.load(sys.stdin)
            result = open_in_editor(payload)
        else:
            raise ValueError(f"unsupported bridge command: {command}")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    print(json.dumps({"ok": True, "result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
