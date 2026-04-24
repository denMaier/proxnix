from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import WorkstationConfig, load_workstation_config
from .errors import PlanningError
from .paths import SitePaths
from .provider_keys import INTERNAL_KEYS_GROUP, container_key_name
from .provider_keys import have_container_private_key, initialize_container_identity
from .secret_provider import container_scope, group_scope, load_secret_provider
from .secret_provider_embedded import EmbeddedSopsProvider
from .site import collect_site_vmids, read_container_secret_groups, valid_secret_group_name


CONFIG_FIELDS = {
    "siteDir": "PROXNIX_SITE_DIR",
    "sopsMasterIdentity": "PROXNIX_SOPS_MASTER_IDENTITY",
    "hosts": "PROXNIX_HOSTS",
    "sshIdentity": "PROXNIX_SSH_IDENTITY",
    "remoteDir": "PROXNIX_REMOTE_DIR",
    "remotePrivDir": "PROXNIX_REMOTE_PRIV_DIR",
    "remoteHostRelayIdentity": "PROXNIX_REMOTE_HOST_RELAY_IDENTITY",
    "secretProvider": "PROXNIX_SECRET_PROVIDER",
    "secretProviderCommand": "PROXNIX_SECRET_PROVIDER_COMMAND",
    "scriptsDir": "PROXNIX_SCRIPTS_DIR",
    "managerPythonPath": "PROXNIX_MANAGER_PYTHONPATH",
}

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


def _shell_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _preserved_config_lines(config_path: Path) -> list[str]:
    if not config_path.is_file():
        return []

    managed_keys = set(CONFIG_FIELDS.values())
    preserved: list[str] = []
    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key = stripped.removeprefix("export ").split("=", 1)[0].strip()
        if key.startswith("PROXNIX_") and key not in managed_keys:
            preserved.append(raw_line)
    return preserved


def _config_payload(config: WorkstationConfig) -> dict[str, object]:
    provider_env = config.provider_environment_map()
    sops_master_identity = (
        provider_env.get("PROXNIX_SOPS_MASTER_IDENTITY")
        or provider_env.get("PROXNIX_MASTER_IDENTITY")
        or ""
    )
    return {
        "siteDir": "" if config.site_dir is None else str(config.site_dir),
        "sopsMasterIdentity": sops_master_identity,
        "hosts": " ".join(config.hosts),
        "sshIdentity": "" if config.ssh_identity is None else str(config.ssh_identity),
        "remoteDir": str(config.remote_dir),
        "remotePrivDir": str(config.remote_priv_dir),
        "remoteHostRelayIdentity": str(config.remote_host_relay_identity),
        "secretProvider": config.secret_provider,
        "secretProviderCommand": config.secret_provider_command or "",
        "scriptsDir": "" if config.scripts_dir is None else str(config.scripts_dir),
        "managerPythonPath": provider_env.get("PROXNIX_MANAGER_PYTHONPATH", ""),
    }


def build_config_state(config_file: Path | None = None) -> dict[str, object]:
    config = load_workstation_config(config_file)
    provider_env = config.provider_environment_map()
    managed_keys = set(CONFIG_FIELDS.values())
    return {
        "path": str(config.config_file),
        "exists": config.config_file.is_file(),
        "config": _config_payload(config),
        "preservedKeys": sorted(key for key in provider_env if key not in managed_keys),
    }


def save_config(config_file: Path | None, values: dict[str, object]) -> dict[str, object]:
    current = build_config_state(config_file)
    raw_config = current["config"]
    assert isinstance(raw_config, dict)
    config = {
        **DEFAULT_CONFIG,
        **{str(key): str(value) for key, value in raw_config.items()},
        **{str(key): str(value) for key, value in values.items()},
    }

    unknown = sorted(set(config) - set(CONFIG_FIELDS))
    if unknown:
        raise ValueError(f"unsupported config field(s): {', '.join(unknown)}")

    config_path = Path(str(current["path"]))
    preserved_lines = _preserved_config_lines(config_path)
    lines = ["# proxnix workstation config"]
    for field, env_key in CONFIG_FIELDS.items():
        value = str(config[field]).strip()
        if value:
            lines.append(f"{env_key}={_shell_single_quoted(value)}")
    if preserved_lines:
        lines.append("")
        lines.extend(preserved_lines)

    before = config_path.read_text(encoding="utf-8") if config_path.is_file() else None
    next_text = "\n".join(lines) + "\n"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(next_text, encoding="utf-8")
    state = build_config_state(config_path)
    state["changed"] = before != next_text
    return state


def set_config_value(config_file: Path | None, key: str, value: str) -> dict[str, object]:
    if key not in CONFIG_FIELDS:
        raise ValueError(f"unsupported config field: {key}")
    return save_config(config_file, {key: value})


def sidebar_metadata_path(config_file: Path | None = None) -> Path:
    config = load_workstation_config(config_file)
    return config.config_file.parent / "manager-sidebar-state.json"


def _normalized_site_key(site_dir: Path | None) -> str:
    if site_dir is None:
        return ""
    return str(site_dir.expanduser().resolve(strict=False))


def _normalize_sidebar_metadata(raw_value: object) -> dict[str, object]:
    raw = raw_value if isinstance(raw_value, dict) else {}

    display_name = str(raw.get("displayName", "")).strip()
    group = str(raw.get("group", "")).strip()
    labels_raw = raw.get("labels", [])
    labels: list[str] = []
    if isinstance(labels_raw, list):
        labels = [str(label).strip() for label in labels_raw if str(label).strip()]

    return {
        "displayName": display_name,
        "group": group,
        "labels": labels,
    }


def read_sidebar_metadata(config_file: Path | None = None) -> dict[str, dict[str, object]]:
    config = load_workstation_config(config_file)
    if config.site_dir is None:
        return {}
    metadata_path = sidebar_metadata_path(config_file)
    if not metadata_path.is_file():
        return {}
    try:
        raw_state = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw_state, dict):
        return {}
    sites = raw_state.get("sites")
    if not isinstance(sites, dict):
        return {}
    site_state = sites.get(_normalized_site_key(config.site_dir))
    if not isinstance(site_state, dict):
        return {}
    containers = site_state.get("containers")
    if not isinstance(containers, dict):
        return {}

    return {
        str(vmid): _normalize_sidebar_metadata(metadata)
        for vmid, metadata in containers.items()
        if str(vmid).isdigit()
    }


def _require_site_paths(config: WorkstationConfig) -> SitePaths:
    return SitePaths.from_config(config)


def _require_vmid(vmid: str) -> str:
    vmid = str(vmid).strip()
    if not vmid.isdigit():
        raise ValueError("Container VMID is required.")
    return vmid


def _require_group(group: str) -> str:
    group = str(group).strip()
    if group == "shared":
        raise ValueError("shared is built in and cannot be changed as a named group")
    if not valid_secret_group_name(group):
        raise ValueError(f"invalid group name: {group}")
    return group


def _write_container_secret_groups(path: Path, groups: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if groups:
        path.write_text("\n".join(groups) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def _delete_sidebar_metadata(config: WorkstationConfig, vmid: str) -> None:
    if config.site_dir is None:
        return
    metadata_path = sidebar_metadata_path(config.config_file)
    if not metadata_path.is_file():
        return
    try:
        state = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(state, dict):
        return
    sites = state.get("sites")
    if not isinstance(sites, dict):
        return
    site_state = sites.get(_normalized_site_key(config.site_dir))
    if not isinstance(site_state, dict):
        return
    containers = site_state.get("containers")
    if not isinstance(containers, dict):
        return
    containers.pop(vmid, None)
    if not containers:
        sites.pop(_normalized_site_key(config.site_dir), None)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def create_site_nix(config_file: Path | None = None) -> dict[str, object]:
    config = load_workstation_config(config_file)
    site_paths = _require_site_paths(config)
    if site_paths.site_nix.exists():
        raise ValueError(f"site.nix already exists: {site_paths.site_nix}")
    site_paths.site_nix.write_text(SITE_NIX_SCAFFOLD, encoding="utf-8")
    return build_status(config.config_file)


def create_secret_group(config_file: Path | None, group: str) -> dict[str, object]:
    config = load_workstation_config(config_file)
    site_paths = _require_site_paths(config)
    group = _require_group(group)
    if config.secret_provider == "embedded-sops":
        (site_paths.private_dir / "groups" / group).mkdir(parents=True, exist_ok=True)
    return build_status(config.config_file)


def delete_secret_group(config_file: Path | None, group: str) -> dict[str, object]:
    config = load_workstation_config(config_file)
    site_paths = _require_site_paths(config)
    group = _require_group(group)
    store = site_paths.group_store(group)
    if store.exists():
        raise ValueError(f"Refusing to delete group {group}: secret store exists.")

    for vmid in collect_site_vmids(site_paths):
        groups_file = site_paths.container_secret_groups_file(vmid)
        groups = read_container_secret_groups(site_paths, vmid)
        if group in groups:
            _write_container_secret_groups(groups_file, [candidate for candidate in groups if candidate != group])

    group_dir = site_paths.private_dir / "groups" / group
    if group_dir.exists():
        try:
            group_dir.rmdir()
        except OSError as exc:
            raise ValueError(f"Refusing to delete non-empty group directory: {group_dir}") from exc
    return build_status(config.config_file)


def attach_secret_group(config_file: Path | None, vmid: str, group: str) -> dict[str, object]:
    config = load_workstation_config(config_file)
    site_paths = _require_site_paths(config)
    vmid = _require_vmid(vmid)
    group = _require_group(group)
    groups_file = site_paths.container_secret_groups_file(vmid)
    groups = read_container_secret_groups(site_paths, vmid)
    if group not in groups:
        groups.append(group)
        _write_container_secret_groups(groups_file, groups)
    return build_status(config.config_file)


def detach_secret_group(config_file: Path | None, vmid: str, group: str) -> dict[str, object]:
    config = load_workstation_config(config_file)
    site_paths = _require_site_paths(config)
    vmid = _require_vmid(vmid)
    group = _require_group(group)
    groups_file = site_paths.container_secret_groups_file(vmid)
    groups = read_container_secret_groups(site_paths, vmid)
    if group in groups:
        _write_container_secret_groups(groups_file, [candidate for candidate in groups if candidate != group])
    return build_status(config.config_file)


def create_container_bundle(config_file: Path | None, vmid: str) -> dict[str, object]:
    config = load_workstation_config(config_file)
    site_paths = _require_site_paths(config)
    vmid = _require_vmid(vmid)
    public_dir = site_paths.container_dir(vmid)
    if public_dir.exists():
        raise ValueError(f"Container bundle already exists: {public_dir}")

    public_dir.mkdir(parents=True)
    (public_dir / "dropins").mkdir()
    provider = load_secret_provider(config, site_paths)
    try:
        initialize_container_identity(config, provider, site_paths, vmid)
    except Exception:
        shutil.rmtree(public_dir, ignore_errors=True)
        raise
    return build_status(config.config_file)


def _container_has_source_secrets(config: WorkstationConfig, site_paths: SitePaths, provider: object, vmid: str) -> bool:
    if isinstance(provider, EmbeddedSopsProvider):
        return site_paths.container_store(vmid).is_file()
    try:
        return bool(provider.list_names(container_scope(vmid)))  # type: ignore[attr-defined]
    except Exception as exc:
        raise ValueError(f"Could not check container-local secrets for {vmid}: {exc}") from exc


def _remove_container_identity(config: WorkstationConfig, site_paths: SitePaths, provider: object, vmid: str) -> None:
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

    if not have_container_private_key(config, provider, site_paths, vmid):  # type: ignore[arg-type]
        return
    provider.remove(group_scope(INTERNAL_KEYS_GROUP), container_key_name(vmid))  # type: ignore[attr-defined]


def delete_container_bundle(config_file: Path | None, vmid: str) -> dict[str, object]:
    config = load_workstation_config(config_file)
    site_paths = _require_site_paths(config)
    vmid = _require_vmid(vmid)
    provider = load_secret_provider(config, site_paths)
    if _container_has_source_secrets(config, site_paths, provider, vmid):
        raise ValueError(f"Refusing to delete container {vmid}: container-local secrets still exist.")

    public_dir = site_paths.container_dir(vmid)
    if public_dir.exists():
        shutil.rmtree(public_dir)
    try:
        _remove_container_identity(config, site_paths, provider, vmid)
    except Exception as exc:
        raise ValueError(f"Container scaffold was removed, but identity deletion failed: {exc}") from exc

    relay_cache_dir = site_paths.relay_cache_dir / "containers" / vmid
    if relay_cache_dir.exists():
        shutil.rmtree(relay_cache_dir)
    _delete_sidebar_metadata(config, vmid)
    return build_status(config.config_file)


def _defined_secret_groups(site_paths: SitePaths) -> list[str]:
    groups_dir = site_paths.private_dir / "groups"
    if not groups_dir.is_dir():
        return []
    return sorted(
        entry.name
        for entry in groups_dir.iterdir()
        if entry.is_dir() and valid_secret_group_name(entry.name)
    )


def _scan_site(config: WorkstationConfig) -> tuple[bool, list[dict[str, object]], list[str], list[str], list[str]]:
    warnings: list[str] = []
    containers: list[dict[str, object]] = []
    attached_groups: set[str] = set()

    if config.site_dir is None:
        warnings.append("Set PROXNIX_SITE_DIR to scan your site repo.")
        return False, containers, [], [], warnings
    if not config.site_dir.exists():
        warnings.append(f"Site directory does not exist: {config.site_dir}")
        return False, containers, [], [], warnings
    if not config.site_dir.is_dir():
        warnings.append(f"Site path is not a directory: {config.site_dir}")
        return False, containers, [], [], warnings

    site_paths = SitePaths(config.site_dir)
    for vmid in collect_site_vmids(site_paths):
        public_dir = site_paths.container_dir(vmid)
        private_container_dir = site_paths.private_dir / "containers" / vmid
        dropins_dir = public_dir / "dropins"
        dropins = sorted(entry.name for entry in dropins_dir.iterdir()) if dropins_dir.is_dir() else []

        try:
            secret_groups = read_container_secret_groups(site_paths, vmid)
        except PlanningError as exc:
            secret_groups = []
            warnings.append(str(exc))
        attached_groups.update(secret_groups)

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

    return True, containers, _defined_secret_groups(site_paths), sorted(attached_groups), warnings


def _site_nix_path(config: WorkstationConfig) -> Path:
    if config.site_dir is None:
        return Path("site.nix")
    return config.site_dir / "site.nix"


def build_status(config_file: Path | None = None) -> dict[str, object]:
    config = load_workstation_config(config_file)
    site_dir_exists, containers, defined_groups, attached_groups, warnings = _scan_site(config)
    site_nix = _site_nix_path(config)
    provider_env = config.provider_environment_map()

    return {
        "configPath": str(config.config_file),
        "configExists": config.config_file.is_file(),
        "siteDirExists": site_dir_exists,
        "siteNixPath": str(site_nix),
        "siteNixExists": site_nix.is_file(),
        "siteNixContent": site_nix.read_text(encoding="utf-8", errors="replace") if site_nix.is_file() else "",
        "preservedConfigKeys": sorted(provider_env),
        "warnings": warnings,
        "config": _config_payload(config),
        "containers": containers,
        "definedSecretGroups": defined_groups,
        "attachedSecretGroups": attached_groups,
        "sidebarMetadata": read_sidebar_metadata(config_file),
    }
