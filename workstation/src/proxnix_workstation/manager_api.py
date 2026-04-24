from __future__ import annotations

from pathlib import Path

from .config import WorkstationConfig, load_workstation_config
from .errors import PlanningError
from .paths import SitePaths
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
}


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
    }


def build_config_state(config_file: Path | None = None) -> dict[str, object]:
    config = load_workstation_config(config_file)
    provider_env = config.provider_environment_map()
    return {
        "path": str(config.config_file),
        "exists": config.config_file.is_file(),
        "config": _config_payload(config),
        "preservedKeys": sorted(provider_env),
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
        "sidebarMetadata": {},
    }
