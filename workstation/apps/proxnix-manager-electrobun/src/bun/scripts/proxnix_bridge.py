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
    "PROXNIX_MANAGER_PYTHONPATH",
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
    "managerPythonPath": "",
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

    entries.extend(_manager_pythonpath_entries())

    deduped: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry not in seen:
            seen.add(entry)
            deduped.append(entry)
    return deduped


def _manager_pythonpath_entries() -> list[str]:
    raw_value = os.environ.get("PROXNIX_MANAGER_PYTHONPATH", "").strip()
    config_path = default_config_path()
    if config_path.is_file():
        try:
            raw_value = parse_config_lines(config_path.read_text(encoding="utf-8")).get(
                "PROXNIX_MANAGER_PYTHONPATH", raw_value
            )
        except ValueError:
            pass

    home = Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    entries: list[str] = []
    for raw_entry in raw_value.split(os.pathsep):
        entry = _expand_home_string(raw_entry.strip(), home)
        if entry:
            entries.extend(_pythonpath_entry_candidates(Path(entry)))
    return entries


def _pythonpath_entry_candidates(path: Path) -> list[str]:
    candidates = [path]

    if path.name in {"python", "python3"} or path.name.startswith("python3."):
        candidates.extend(_venv_site_packages_from_bin(path.parent))
    elif path.name == "bin":
        candidates.extend(_venv_site_packages_from_bin(path))

    return [str(candidate) for candidate in candidates]


def _venv_site_packages_from_bin(bin_dir: Path) -> list[Path]:
    venv_dir = bin_dir.parent
    patterns = [
        venv_dir / "lib" / "python*" / "site-packages",
        venv_dir / "Lib" / "site-packages",
    ]

    paths: list[Path] = []
    for pattern in patterns:
        for match in glob.glob(str(pattern)):
            match_path = Path(match)
            if match_path.is_dir():
                paths.append(match_path)
    return sorted(set(paths))


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
        "managerPythonPath": value_for("PROXNIX_MANAGER_PYTHONPATH").strip(),
    }

    return payload, preserved_keys, config_path


def valid_secret_group_name(value: str) -> bool:
    return bool(value) and SECRET_GROUP_RE.fullmatch(value) is not None


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


def create_secret_group(payload: object) -> dict[str, object]:
    group = _secret_group_from_payload(payload)
    cli_result = _cli_site_snapshot(["site", "group", "create", group])
    if cli_result is None:
        raise ValueError(f"failed to create secret group: {group}")
    return cli_result


def create_container_bundle(payload: object) -> dict[str, object]:
    vmid = _vmid_from_payload(payload)
    cli_result = _cli_site_snapshot(["site", "container", "create", vmid])
    if cli_result is None:
        raise ValueError(f"failed to create container bundle: {vmid}")
    return cli_result


def delete_container_bundle(payload: object) -> dict[str, object]:
    vmid = _vmid_from_payload(payload)
    cli_result = _cli_site_snapshot(["site", "container", "delete", vmid])
    if cli_result is None:
        raise ValueError(f"failed to delete container bundle: {vmid}")
    return cli_result


def delete_secret_group(payload: object) -> dict[str, object]:
    group = _secret_group_from_payload(payload)
    cli_result = _cli_site_snapshot(["site", "group", "delete", group])
    if cli_result is None:
        raise ValueError(f"failed to delete secret group: {group}")
    return cli_result


def attach_secret_group(payload: object) -> dict[str, object]:
    group = _secret_group_from_payload(payload)
    vmid = _vmid_from_payload(payload)
    cli_result = _cli_site_snapshot(["site", "group", "attach", vmid, group])
    if cli_result is None:
        raise ValueError(f"failed to attach secret group {group} to {vmid}")
    return cli_result


def detach_secret_group(payload: object) -> dict[str, object]:
    group = _secret_group_from_payload(payload)
    vmid = _vmid_from_payload(payload)
    cli_result = _cli_site_snapshot(["site", "group", "detach", vmid, group])
    if cli_result is None:
        raise ValueError(f"failed to detach secret group {group} from {vmid}")
    return cli_result


def secrets_provider_status() -> dict[str, object]:
    cli_result = _cli_secrets_provider_status()
    if cli_result is None:
        raise ValueError("failed to load secret provider status")
    return cli_result


def snapshot() -> dict[str, object]:
    cli_snapshot = _cli_status()
    if cli_snapshot is None:
        raise ValueError("failed to load proxnix status")
    return cli_snapshot


def create_site_nix(_payload: object) -> dict[str, object]:
    cli_result = _cli_site_snapshot(["site", "create-site-nix"])
    if cli_result is None:
        raise ValueError("failed to create site.nix")
    return cli_result


def save_config(payload: dict[str, object]) -> dict[str, object]:
    cli_snapshot = _cli_save_config(payload)
    if cli_snapshot is None:
        raise ValueError("failed to save config")
    return cli_snapshot


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
    stdin_text: str | None = None,
) -> tuple[dict[str, object] | None, str, int]:
    command, env = _proxnix_command()
    if stdin_payload is not None and stdin_text is not None:
        return None, "internal bridge error: duplicate stdin payloads", 1
    input_text = stdin_text if stdin_text is not None else (None if stdin_payload is None else json.dumps(stdin_payload))
    try:
        stdout, stderr, exit_code = _run_cli(
            [*command, *args],
            timeout=timeout,
            env=env,
            stdin_text=input_text,
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
    stdin_text: str | None = None,
) -> tuple[dict[str, object] | None, str, int]:
    payload, stderr, exit_code = _run_json_cli(
        args,
        timeout=timeout,
        stdin_payload=stdin_payload,
        stdin_text=stdin_text,
    )
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


def _cli_secrets_provider_status() -> dict[str, object] | None:
    data, _error, exit_code = _json_cli_data(
        ["secrets", "status", "--json"],
        timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
    )
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


def run_doctor(payload: object) -> dict[str, object]:
    cli_result = _cli_run_validation(payload)
    if cli_result is None:
        raise ValueError("failed to run validation")
    return cli_result


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


def _cli_secret_scope_status(payload: object) -> dict[str, object] | None:
    try:
        scope_type, scope_id = _secret_scope_payload(payload)
    except ValueError:
        return None
    args = ["secrets", "scope-status", "--scope", scope_type, "--json"]
    if scope_id:
        args.extend(["--id", scope_id])
    data, _error, exit_code = _json_cli_data(
        args,
        timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
    )
    if data is None or exit_code not in {0, 1, 2}:
        return None
    return data


def _cli_secret_command_result(
    payload: object,
    action: str,
    *,
    stdin_text: str | None = None,
) -> dict[str, object] | None:
    try:
        scope_type, scope_id = _secret_scope_payload(payload)
    except ValueError:
        return None
    opts = payload if isinstance(payload, dict) else {}
    name = str(opts.get("name", "")).strip()
    args = ["secrets", *_secret_args_for(scope_type, scope_id, action, name or None), "--json"]
    data, error, exit_code = _json_cli_data(
        args,
        timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
        stdin_text=stdin_text,
    )
    if data is None:
        return {"output": "", "exitCode": exit_code or 1, "error": error}
    return {
        "output": str(data.get("output", "")).strip(),
        "exitCode": int(data.get("exitCode", exit_code) or exit_code),
        "error": str(data.get("error", "")).strip(),
    }


def _cli_init_container_identity(payload: object) -> dict[str, object] | None:
    opts = payload if isinstance(payload, dict) else {}
    vmid = str(opts.get("vmid", "")).strip()
    if not vmid.isdigit():
        return None
    data, error, exit_code = _json_cli_data(
        ["secrets", "init-container", vmid, "--json"],
        timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS,
    )
    if data is None:
        return {"output": "", "exitCode": exit_code or 1, "error": error}
    return {
        "output": str(data.get("output", "")).strip(),
        "exitCode": int(data.get("exitCode", exit_code) or exit_code),
        "error": str(data.get("error", "")).strip(),
    }


def _cli_site_snapshot(args: list[str]) -> dict[str, object] | None:
    data, _error, exit_code = _json_cli_data(args, timeout=INTERACTIVE_SECRET_BACKEND_TIMEOUT_SECONDS)
    if data is None or exit_code != 0:
        return None
    return data


def run_publish(payload: object) -> dict[str, object]:
    cli_result = _cli_run_publish(payload)
    if cli_result is None:
        raise ValueError("failed to run publish")
    return cli_result


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


def secret_scope_status(payload: object) -> dict[str, object]:
    cli_result = _cli_secret_scope_status(payload)
    if cli_result is None:
        raise ValueError("failed to load secret scope status")
    return cli_result


def set_secret(payload: object) -> dict[str, object]:
    scope_type, scope_id = _secret_scope_payload(payload)
    opts = payload if isinstance(payload, dict) else {}
    name = str(opts.get("name", "")).strip()
    value = str(opts.get("value", ""))
    if not name:
        return {"output": "", "exitCode": 1, "error": "Secret name is required."}
    if value == "":
        return {"output": "", "exitCode": 1, "error": "Secret value cannot be empty."}

    cli_result = _cli_secret_command_result(payload, "set", stdin_text=value)
    if cli_result is None:
        raise ValueError(f"failed to set secret: {name}")
    return cli_result


def remove_secret(payload: object) -> dict[str, object]:
    scope_type, scope_id = _secret_scope_payload(payload)
    opts = payload if isinstance(payload, dict) else {}
    name = str(opts.get("name", "")).strip()
    if not name:
        return {"output": "", "exitCode": 1, "error": "Secret name is required."}

    cli_result = _cli_secret_command_result(payload, "rm")
    if cli_result is None:
        raise ValueError(f"failed to remove secret: {name}")
    return cli_result


def rotate_secret_scope(payload: object) -> dict[str, object]:
    scope_type, scope_id = _secret_scope_payload(payload)
    cli_result = _cli_secret_command_result(payload, "rotate")
    if cli_result is None:
        raise ValueError("failed to rotate secret store")
    return cli_result


def init_container_identity(payload: object) -> dict[str, object]:
    opts = payload if isinstance(payload, dict) else {}
    vmid = str(opts.get("vmid", "")).strip()
    if not vmid.isdigit():
        return {"output": "", "exitCode": 1, "error": "Container VMID is required."}

    cli_result = _cli_init_container_identity(payload)
    if cli_result is None:
        raise ValueError(f"failed to initialize identity for {vmid}")
    return cli_result


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
