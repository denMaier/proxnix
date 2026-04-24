#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import glob
import re
import shutil
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

SITE_NIX_SCAFFOLD = """\
{ ... }: {
}
"""

SITE_README_SCAFFOLD = """\
# proxnix site

This repository contains the site state published by Proxnix Manager.
"""

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
    env = _with_tool_path(env)
    extra_entries = _pythonpath_entries()
    if extra_entries:
        existing = env.get("PYTHONPATH", "").strip()
        combined = extra_entries.copy()
        if existing:
            combined.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(combined)
    return env


def _with_tool_path(env: dict[str, str]) -> dict[str, str]:
    extra_paths = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    existing = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    merged: list[str] = []
    for path in [*extra_paths, *existing]:
        if path not in merged:
            merged.append(path)
    env["PATH"] = os.pathsep.join(merged)
    return env


def _command_output(stdout: str, stderr: str) -> str:
    parts = [part.strip() for part in (stdout, stderr) if part.strip()]
    return "\n".join(parts)


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env = _with_tool_path(env)
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


def _secrets_command() -> tuple[list[str], dict[str, str]]:
    bundled_cli = _bundled_cli("proxnix-secrets")
    if bundled_cli is not None:
        return [str(bundled_cli)], _subprocess_env()
    repo_cli = _repo_cli("proxnix-secrets")
    if repo_cli is not None:
        return [str(repo_cli)], _subprocess_env()
    return [sys.executable, "-m", "proxnix_workstation.secrets_cli"], _subprocess_env()


def _proxnix_command() -> tuple[list[str], dict[str, str]]:
    bundled_cli = _bundled_cli("proxnix")
    if bundled_cli is not None:
        return [str(bundled_cli)], _subprocess_env()
    repo_cli = _repo_cli("proxnix")
    if repo_cli is not None:
        return [str(repo_cli)], _subprocess_env()
    return [sys.executable, "-m", "proxnix_workstation.cli"], _subprocess_env()


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


def _site_dir_from_config(config: dict[str, str]) -> Path:
    site_dir_raw = config["siteDir"]
    if not site_dir_raw:
        raise ValueError("Set site directory first.")
    site_dir = Path(site_dir_raw).expanduser()
    if not site_dir.is_dir():
        raise ValueError(f"Site path is not a directory: {site_dir}")
    return site_dir


def _secret_group_from_payload(payload: object) -> str:
    opts = payload if isinstance(payload, dict) else {}
    group = str(opts.get("group", "")).strip()
    if group == "shared":
        raise ValueError("shared is built in and cannot be changed as a named group")
    if not valid_secret_group_name(group):
        raise ValueError(f"invalid group name: {group}")
    return group


def _vmid_from_payload(payload: object) -> str:
    opts = payload if isinstance(payload, dict) else {}
    vmid = str(opts.get("vmid", "")).strip()
    if not vmid.isdigit():
        raise ValueError("Container VMID is required.")
    return vmid


def _delete_sidebar_metadata(config: dict[str, str], vmid: str) -> None:
    site_dir = config["siteDir"]
    if not site_dir:
        return
    state = load_sidebar_state()
    sites = state.get("sites")
    if not isinstance(sites, dict):
        return
    site_key = _normalized_site_key(site_dir)
    site_state = sites.get(site_key)
    if not isinstance(site_state, dict):
        return
    containers = site_state.get("containers")
    if not isinstance(containers, dict):
        return
    containers.pop(vmid, None)
    if not containers:
        sites.pop(site_key, None)
    metadata_path = sidebar_metadata_path()
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _container_secret_groups_file(site_dir: Path, vmid: str) -> Path:
    return site_dir / "containers" / vmid / "secret-groups.list"


def _write_container_secret_groups(path: Path, groups: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if groups:
        path.write_text("\n".join(groups) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def create_secret_group(payload: object) -> dict[str, object]:
    group = _secret_group_from_payload(payload)
    config, _, _ = read_config_payload()
    site_dir = _site_dir_from_config(config)
    if config["secretProvider"] == "embedded-sops":
        (site_dir / "private" / "groups" / group).mkdir(parents=True, exist_ok=True)
    return snapshot()


def _load_container_identity_context() -> tuple[object, object, object]:
    ctx, provider_error = _load_provider_context()
    if provider_error:
        raise ValueError(f"Secret backend unavailable: {provider_error}")
    assert ctx is not None
    return ctx


def _remove_container_identity(ctx: tuple[object, object, object], vmid: str) -> None:
    config, site_paths, provider = ctx
    from proxnix_workstation.provider_keys import (
        INTERNAL_KEYS_GROUP,
        container_key_name,
        have_container_private_key,
    )
    from proxnix_workstation.secret_provider_embedded import EmbeddedSopsProvider
    from proxnix_workstation.secret_provider_types import group_scope

    if isinstance(provider, EmbeddedSopsProvider):
        store = site_paths.container_identity_store(vmid)
        if store.exists():
            store.unlink()
        container_private_dir = site_paths.private_dir / "containers" / vmid
        if container_private_dir.exists():
            try:
                container_private_dir.rmdir()
            except OSError:
                pass
        return

    if not have_container_private_key(config, provider, site_paths, vmid):
        return
    provider.remove(group_scope(INTERNAL_KEYS_GROUP), container_key_name(vmid))


def _container_has_source_secrets(ctx: tuple[object, object, object], vmid: str) -> bool:
    config, site_paths, provider = ctx
    from proxnix_workstation.secret_provider import container_scope
    from proxnix_workstation.secret_provider_embedded import EmbeddedSopsProvider

    if isinstance(provider, EmbeddedSopsProvider):
        return site_paths.container_store(vmid).is_file()
    try:
        return bool(provider.list_names(container_scope(vmid)))
    except Exception as exc:
        raise ValueError(f"Could not check container-local secrets for {vmid}: {exc}") from exc


def create_container_bundle(payload: object) -> dict[str, object]:
    vmid = _vmid_from_payload(payload)
    config, _, _ = read_config_payload()
    site_dir = _site_dir_from_config(config)
    public_dir = site_dir / "containers" / vmid
    if public_dir.exists():
        raise ValueError(f"Container bundle already exists: {public_dir}")

    public_dir.mkdir(parents=True)
    (public_dir / "dropins").mkdir()

    ctx = _load_container_identity_context()
    config_obj, site_paths, provider = ctx
    from proxnix_workstation.provider_keys import initialize_container_identity

    try:
        initialize_container_identity(config_obj, provider, site_paths, vmid)
    except Exception:
        shutil.rmtree(public_dir, ignore_errors=True)
        raise

    return snapshot()


def delete_container_bundle(payload: object) -> dict[str, object]:
    vmid = _vmid_from_payload(payload)
    config, _, _ = read_config_payload()
    site_dir = _site_dir_from_config(config)
    ctx = _load_container_identity_context()
    config_obj, site_paths, _provider = ctx
    if _container_has_source_secrets(ctx, vmid):
        raise ValueError(f"Refusing to delete container {vmid}: container-local secrets still exist.")

    public_dir = site_dir / "containers" / vmid
    if public_dir.exists():
        shutil.rmtree(public_dir)

    try:
        _remove_container_identity(ctx, vmid)
    except Exception as exc:
        raise ValueError(f"Container scaffold was removed, but identity deletion failed: {exc}") from exc

    relay_cache_dir = site_paths.relay_cache_dir / "containers" / vmid
    if relay_cache_dir.exists():
        shutil.rmtree(relay_cache_dir)

    _delete_sidebar_metadata(config, vmid)
    return snapshot()


def delete_secret_group(payload: object) -> dict[str, object]:
    group = _secret_group_from_payload(payload)
    config, _, _ = read_config_payload()
    site_dir = _site_dir_from_config(config)
    store = site_dir / "private" / "groups" / group / "secrets.sops.yaml"
    if store.exists():
        raise ValueError(f"Refusing to delete group {group}: secret store exists.")

    containers_dir = site_dir / "containers"
    if containers_dir.is_dir():
        for entry in containers_dir.iterdir():
            if not (entry.is_dir() and entry.name.isdigit()):
                continue
            groups_file = entry / "secret-groups.list"
            groups = read_container_secret_groups(groups_file)
            if group in groups:
                _write_container_secret_groups(groups_file, [candidate for candidate in groups if candidate != group])

    group_dir = site_dir / "private" / "groups" / group
    if group_dir.exists():
        try:
            group_dir.rmdir()
        except OSError as exc:
            raise ValueError(f"Refusing to delete non-empty group directory: {group_dir}") from exc
    return snapshot()


def attach_secret_group(payload: object) -> dict[str, object]:
    group = _secret_group_from_payload(payload)
    vmid = _vmid_from_payload(payload)
    config, _, _ = read_config_payload()
    site_dir = _site_dir_from_config(config)
    groups_file = _container_secret_groups_file(site_dir, vmid)
    groups = read_container_secret_groups(groups_file)
    if group not in groups:
        groups.append(group)
        _write_container_secret_groups(groups_file, groups)
    return snapshot()


def detach_secret_group(payload: object) -> dict[str, object]:
    group = _secret_group_from_payload(payload)
    vmid = _vmid_from_payload(payload)
    config, _, _ = read_config_payload()
    site_dir = _site_dir_from_config(config)
    groups_file = _container_secret_groups_file(site_dir, vmid)
    groups = read_container_secret_groups(groups_file)
    if group in groups:
        _write_container_secret_groups(groups_file, [candidate for candidate in groups if candidate != group])
    return snapshot()


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
        from proxnix_workstation.provider_keys import (
            INTERNAL_KEYS_GROUP,
            container_key_name,
            have_container_private_key,
        )
        from proxnix_workstation.secret_provider_embedded import EmbeddedSopsProvider
        from proxnix_workstation.secret_provider_types import group_scope

        if isinstance(provider, EmbeddedSopsProvider):
            store = site_paths.container_identity_store(vmid)
            if store.is_file():
                return True
            return have_container_private_key(config, provider, site_paths, vmid)

        key_name = container_key_name(vmid)
        internal_keys = group_scope(INTERNAL_KEYS_GROUP)
        try:
            if key_name in provider.list_names(internal_keys):
                return True
        except Exception:
            pass
        try:
            return provider.get(internal_keys, key_name) is not None
        except Exception:
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
    cli_snapshot = _cli_status()
    if cli_snapshot is not None:
        return cli_snapshot

    config, preserved_keys, config_path = read_config_payload()
    site_dir_exists, containers, defined_groups, attached_groups, warnings = scan_state(config)
    sidebar_metadata = read_sidebar_metadata(config["siteDir"])
    site_nix = Path(config["siteDir"]).expanduser() / "site.nix" if config["siteDir"] else Path("site.nix")
    site_nix_content = site_nix.read_text(encoding="utf-8", errors="replace") if site_nix.is_file() else ""

    return {
        "configPath": str(config_path),
        "configExists": config_path.is_file(),
        "siteDirExists": site_dir_exists,
        "siteNixPath": str(site_nix),
        "siteNixExists": site_nix.is_file(),
        "siteNixContent": site_nix_content,
        "preservedConfigKeys": preserved_keys,
        "warnings": warnings,
        "config": config,
        "containers": containers,
        "definedSecretGroups": defined_groups,
        "attachedSecretGroups": attached_groups,
        "sidebarMetadata": sidebar_metadata,
    }


def create_site_nix(_payload: object) -> dict[str, object]:
    config, _, _ = read_config_payload()
    site_dir = _site_dir_from_config(config)
    site_nix = site_dir / "site.nix"
    if site_nix.exists():
        raise ValueError(f"site.nix already exists: {site_nix}")
    site_nix.write_text(SITE_NIX_SCAFFOLD, encoding="utf-8")
    return snapshot()


def save_config(payload: dict[str, object]) -> dict[str, object]:
    cli_snapshot = _cli_save_config(payload)
    if cli_snapshot is not None:
        return cli_snapshot

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


def _scaffold_site_repo(site_dir: Path) -> list[str]:
    actions: list[str] = []
    if not site_dir.exists():
        site_dir.mkdir(parents=True)
        actions.append(f"Created site directory: {site_dir}")
    elif not site_dir.is_dir():
        raise ValueError(f"Site path is not a directory: {site_dir}")

    for directory in (
        site_dir / "containers",
        site_dir / "private" / "shared",
        site_dir / "private" / "groups",
        site_dir / "private" / "containers",
    ):
        if not directory.exists():
            directory.mkdir(parents=True)
            actions.append(f"Created {directory.relative_to(site_dir)}")

    site_nix = site_dir / "site.nix"
    if not site_nix.exists():
        site_nix.write_text(SITE_NIX_SCAFFOLD, encoding="utf-8")
        actions.append("Created site.nix")

    readme = site_dir / "README.md"
    if not readme.exists():
        readme.write_text(SITE_README_SCAFFOLD, encoding="utf-8")
        actions.append("Created README.md")

    if not (site_dir / ".git").exists():
        output, exit_code = _run_git(site_dir, "init")
        if exit_code != 0:
            raise ValueError(output or "git init failed")
        actions.append("Initialized git repository")

    return actions


def _ensure_master_key(config: object, provider: object, site_paths: object) -> str:
    from proxnix_workstation.provider_keys import (
        MASTER_KEY_NAME,
        _provider_get_key,
        _provider_set_key,
        master_private_key_text,
        sops_master_identity_path,
    )
    from proxnix_workstation.secret_provider_embedded import EmbeddedSopsProvider
    from proxnix_workstation.sops_ops import generate_identity_keypair

    if isinstance(provider, EmbeddedSopsProvider):
        identity_path = sops_master_identity_path(config)
        if identity_path.exists():
            master_private_key_text(config, provider)
            return f"Using existing master identity: {identity_path}"
        private_text, _pubkey = generate_identity_keypair()
        identity_path.parent.mkdir(parents=True, exist_ok=True)
        identity_path.write_text(private_text, encoding="utf-8")
        identity_path.chmod(0o600)
        return f"Created master identity: {identity_path}"

    if _provider_get_key(provider, MASTER_KEY_NAME) is not None:
        return "Using existing provider master key"
    private_text, _pubkey = generate_identity_keypair()
    _provider_set_key(provider, MASTER_KEY_NAME, private_text)
    return "Created provider master key"


def run_onboarding(payload: dict[str, object]) -> dict[str, object]:
    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        raise ValueError("run-onboarding requires a config object")

    config = {**DEFAULT_CONFIG, **{str(key): str(value) for key, value in raw_config.items()}}
    site_dir = Path(config["siteDir"]).expanduser()
    if not str(config["siteDir"]).strip():
        raise ValueError("Choose a site directory first.")
    if config["secretProvider"] == "embedded-sops" and not config["sopsMasterIdentity"].strip():
        config["sopsMasterIdentity"] = str(Path.home() / ".ssh" / "proxnix-master")
    if config["secretProvider"] == "exec" and not config["secretProviderCommand"].strip():
        raise ValueError("Exec secret backend requires a provider command.")

    actions = _scaffold_site_repo(site_dir)
    save_config({"config": config})

    _ensure_pythonpath_bootstrap()
    from proxnix_workstation.config import load_workstation_config
    from proxnix_workstation.paths import SitePaths
    from proxnix_workstation.provider_keys import initialize_host_relay_identity
    from proxnix_workstation.secret_provider import load_secret_provider

    workstation_config = load_workstation_config()
    site_paths = SitePaths.from_config(workstation_config)
    provider = load_secret_provider(workstation_config, site_paths)

    actions.append(_ensure_master_key(workstation_config, provider, site_paths))
    try:
        label, pubkey = initialize_host_relay_identity(workstation_config, provider, site_paths)
        actions.append(f"Created {label} identity: {pubkey}")
    except Exception as exc:
        if "already exists" in str(exc):
            actions.append("Using existing host relay identity")
        else:
            raise

    current = snapshot()
    return {
        "snapshot": current,
        "actions": actions,
        "output": "\n".join(actions),
    }


def _run_cli(
    args: list[str],
    *,
    timeout: int = 120,
    env: dict[str, str] | None = None,
    stdin_text: str | None = None,
) -> tuple[str, str, int]:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        input=stdin_text,
    )
    return result.stdout, result.stderr, result.returncode


def _run_json_cli(
    args: list[str],
    *,
    timeout: int = 120,
    stdin_payload: object | None = None,
) -> tuple[dict[str, object] | None, str, int]:
    command, env = _proxnix_command()
    stdin_text = None if stdin_payload is None else json.dumps(stdin_payload)
    try:
        stdout, stderr, exit_code = _run_cli(
            [*command, *args],
            timeout=timeout,
            env=env,
            stdin_text=stdin_text,
        )
    except subprocess.TimeoutExpired:
        return None, f"proxnix {' '.join(args)} timed out.", 124
    except Exception as exc:
        return None, str(exc), 1

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None, _command_output(stdout, stderr) or "proxnix returned invalid JSON.", exit_code or 1
    if not isinstance(payload, dict):
        return None, "proxnix returned invalid JSON shape.", exit_code or 1
    return payload, stderr.strip(), exit_code


def _json_cli_data(
    args: list[str],
    *,
    timeout: int = 120,
    stdin_payload: object | None = None,
) -> tuple[dict[str, object] | None, str, int]:
    payload, stderr, exit_code = _run_json_cli(args, timeout=timeout, stdin_payload=stdin_payload)
    if payload is None:
        return None, stderr, exit_code
    if payload.get("ok") is not True:
        error = payload.get("error")
        if isinstance(error, dict):
            return None, str(error.get("message", "proxnix command failed")), exit_code or 1
        return None, stderr or "proxnix command failed", exit_code or 1
    data = payload.get("data")
    if not isinstance(data, dict):
        return None, "proxnix returned non-object data.", exit_code or 1
    return data, stderr, exit_code


def _cli_status() -> dict[str, object] | None:
    data, _error, exit_code = _json_cli_data(["status", "--json"])
    if data is None or exit_code not in {0, 1, 2}:
        return None
    return data


def _cli_save_config(payload: dict[str, object]) -> dict[str, object] | None:
    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        return None
    data, _error, exit_code = _json_cli_data(
        ["config", "set", "--stdin-json", "--json"],
        stdin_payload={"config": raw_config},
    )
    if data is None or exit_code != 0:
        return None
    return _cli_status()


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
    cli_result = _cli_run_validation(payload)
    if cli_result is not None:
        return cli_result

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


def _cli_run_validation(payload: object) -> dict[str, object] | None:
    args = ["validation", "--site-only", "--json"]
    opts = payload if isinstance(payload, dict) else {}
    if opts.get("configOnly"):
        args.append("--config-only")
    vmid = opts.get("vmid")
    if vmid:
        args.extend(["--vmid", str(vmid)])

    data, error, exit_code = _json_cli_data(
        args,
        timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
    )
    if data is None:
        return {
            "sections": [],
            "oks": 0,
            "warns": 0,
            "fails": 0,
            "exitCode": exit_code or 1,
            "error": error,
        }
    data["exitCode"] = data.get("exitCode", exit_code)
    if exit_code not in {0, int(data.get("exitCode", 0) or 0)} and error:
        data["error"] = error
    return data


def run_publish(payload: object) -> dict[str, object]:
    cli_result = _cli_run_publish(payload)
    if cli_result is not None:
        return cli_result

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


def _cli_run_publish(payload: object) -> dict[str, object] | None:
    opts = payload if isinstance(payload, dict) else {}
    args = ["diff" if opts.get("dryRun") else "sync", "--json"]
    if opts.get("configOnly"):
        args.append("--config-only")
    vmid = opts.get("vmid")
    if vmid:
        args.extend(["--vmid", str(vmid)])
    for host in opts.get("hosts") or []:
        args.append(str(host))

    data, error, exit_code = _json_cli_data(
        args,
        timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
    )
    if data is None:
        return {
            "output": "",
            "exitCode": exit_code or 1,
            "error": error,
        }
    output = str(data.get("output", "")).strip()
    return {
        "output": output or ("Dry run complete." if opts.get("dryRun") else "Publish complete."),
        "exitCode": int(data.get("exitCode", exit_code) or exit_code),
        "error": "",
    }


def _secret_scope_payload(payload: object) -> tuple[str, str]:
    opts = payload if isinstance(payload, dict) else {}
    scope_type = str(opts.get("scopeType", "")).strip()
    scope_id = str(opts.get("scopeId", "")).strip()
    if scope_type not in {"shared", "group", "container"}:
        raise ValueError("secret scope must be shared, group, or container")
    if scope_type in {"group", "container"} and not scope_id:
        raise ValueError(f"{scope_type} secret scope requires an id")
    if scope_type == "group" and not valid_secret_group_name(scope_id):
        raise ValueError(f"invalid group name: {scope_id}")
    if scope_type == "container" and not scope_id.isdigit():
        raise ValueError(f"invalid container VMID: {scope_id}")
    return scope_type, scope_id


def _secret_args_for(scope_type: str, scope_id: str, base: str, name: str | None = None) -> list[str]:
    if base == "ls":
        if scope_type == "shared":
            return ["ls-shared"]
        if scope_type == "group":
            return ["ls-group", scope_id]
        return ["ls", scope_id]
    if base == "set":
        if not name:
            raise ValueError("secret name is required")
        if scope_type == "shared":
            return ["set-shared", name]
        if scope_type == "group":
            return ["set-group", scope_id, name]
        return ["set", scope_id, name]
    if base == "rm":
        if not name:
            raise ValueError("secret name is required")
        if scope_type == "shared":
            return ["rm-shared", name]
        if scope_type == "group":
            return ["rm-group", scope_id, name]
        return ["rm", scope_id, name]
    if base == "rotate":
        if scope_type == "shared":
            return ["rotate-shared"]
        if scope_type == "group":
            return ["rotate-group", scope_id]
        return ["rotate", scope_id]
    raise ValueError(f"unsupported secret action: {base}")


def _parse_secret_entries(scope_type: str, output: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "\t" in line:
            name, source = line.split("\t", 1)
        else:
            name = line
            source = scope_type
        name = name.strip()
        source = source.strip() or scope_type
        key = (name, source)
        if name and key not in seen:
            seen.add(key)
            entries.append({"name": name, "source": source})
    return entries


def secret_scope_status(payload: object) -> dict[str, object]:
    config, _, _ = read_config_payload()
    if not config["siteDir"]:
        return {
            "scopeType": "shared",
            "scopeId": "",
            "entries": [],
            "canRotate": config["secretProvider"] == "embedded-sops",
            "warnings": ["Set site directory first."],
        }

    scope_type, scope_id = _secret_scope_payload(payload)
    args, env = _secrets_command()
    args.extend(_secret_args_for(scope_type, scope_id, "ls"))
    try:
        stdout, stderr, exit_code = _run_cli(
            args,
            timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "scopeType": scope_type,
            "scopeId": scope_id,
            "entries": [],
            "canRotate": config["secretProvider"] == "embedded-sops",
            "warnings": ["Secret listing timed out after 60 minutes."],
        }

    warnings = []
    if exit_code != 0:
        warnings.append(_command_output(stdout, stderr) or "Could not list secrets.")

    return {
        "scopeType": scope_type,
        "scopeId": scope_id,
        "entries": _parse_secret_entries(scope_type, stdout if exit_code == 0 else ""),
        "canRotate": config["secretProvider"] == "embedded-sops",
        "warnings": warnings,
    }


def set_secret(payload: object) -> dict[str, object]:
    scope_type, scope_id = _secret_scope_payload(payload)
    opts = payload if isinstance(payload, dict) else {}
    name = str(opts.get("name", "")).strip()
    value = str(opts.get("value", ""))
    if not name:
        return {"output": "", "exitCode": 1, "error": "Secret name is required."}
    if value == "":
        return {"output": "", "exitCode": 1, "error": "Secret value cannot be empty."}

    args, env = _secrets_command()
    args.extend(_secret_args_for(scope_type, scope_id, "set", name))
    try:
        stdout, stderr, exit_code = _run_cli(
            args,
            timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
            env=env,
            stdin_text=value,
        )
    except subprocess.TimeoutExpired:
        return {"output": "", "exitCode": 1, "error": "Setting secret timed out after 60 minutes."}
    output = _command_output(stdout, stderr)
    return _command_result(output, exit_code, f"Set secret {name}.")


def remove_secret(payload: object) -> dict[str, object]:
    scope_type, scope_id = _secret_scope_payload(payload)
    opts = payload if isinstance(payload, dict) else {}
    name = str(opts.get("name", "")).strip()
    if not name:
        return {"output": "", "exitCode": 1, "error": "Secret name is required."}

    args, env = _secrets_command()
    args.extend(_secret_args_for(scope_type, scope_id, "rm", name))
    try:
        stdout, stderr, exit_code = _run_cli(
            args,
            timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"output": "", "exitCode": 1, "error": "Removing secret timed out after 60 minutes."}
    output = _command_output(stdout, stderr)
    return _command_result(output, exit_code, f"Removed secret {name}.")


def rotate_secret_scope(payload: object) -> dict[str, object]:
    scope_type, scope_id = _secret_scope_payload(payload)
    args, env = _secrets_command()
    args.extend(_secret_args_for(scope_type, scope_id, "rotate"))
    try:
        stdout, stderr, exit_code = _run_cli(
            args,
            timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"output": "", "exitCode": 1, "error": "Rotating secret store timed out after 60 minutes."}
    output = _command_output(stdout, stderr)
    return _command_result(output, exit_code, "Secret store rotated.")


def init_container_identity(payload: object) -> dict[str, object]:
    opts = payload if isinstance(payload, dict) else {}
    vmid = str(opts.get("vmid", "")).strip()
    if not vmid.isdigit():
        return {"output": "", "exitCode": 1, "error": "Container VMID is required."}

    args, env = _secrets_command()
    args.extend(["init-container", vmid])
    try:
        stdout, stderr, exit_code = _run_cli(
            args,
            timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"output": "", "exitCode": 1, "error": "Identity initialization timed out after 60 minutes."}
    output = _command_output(stdout, stderr)
    return _command_result(output, exit_code, f"Initialized identity for {vmid}.")


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
        elif command == "secret-scope-status":
            payload = json.load(sys.stdin)
            result = secret_scope_status(payload)
        elif command == "set-secret":
            payload = json.load(sys.stdin)
            result = set_secret(payload)
        elif command == "remove-secret":
            payload = json.load(sys.stdin)
            result = remove_secret(payload)
        elif command == "rotate-secret-scope":
            payload = json.load(sys.stdin)
            result = rotate_secret_scope(payload)
        elif command == "init-container-identity":
            payload = json.load(sys.stdin)
            result = init_container_identity(payload)
        elif command == "create-container-bundle":
            payload = json.load(sys.stdin)
            result = create_container_bundle(payload)
        elif command == "delete-container-bundle":
            payload = json.load(sys.stdin)
            result = delete_container_bundle(payload)
        elif command == "create-secret-group":
            payload = json.load(sys.stdin)
            result = create_secret_group(payload)
        elif command == "delete-secret-group":
            payload = json.load(sys.stdin)
            result = delete_secret_group(payload)
        elif command == "attach-secret-group":
            payload = json.load(sys.stdin)
            result = attach_secret_group(payload)
        elif command == "detach-secret-group":
            payload = json.load(sys.stdin)
            result = detach_secret_group(payload)
        elif command == "save-config":
            payload = json.load(sys.stdin)
            result = save_config(payload)
        elif command == "run-onboarding":
            payload = json.load(sys.stdin)
            result = run_onboarding(payload)
        elif command == "create-site-nix":
            result = create_site_nix(None)
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
