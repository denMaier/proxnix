from __future__ import annotations

from pathlib import Path

from .config import WorkstationConfig, load_workstation_config
from .errors import PlanningError
from .paths import SitePaths
from .site import collect_site_vmids, read_container_secret_groups, valid_secret_group_name


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
