from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import WorkstationConfig


@dataclass(frozen=True)
class SitePaths:
    site_dir: Path

    @classmethod
    def from_config(cls, config: WorkstationConfig) -> "SitePaths":
        return cls(site_dir=config.require_site_dir())

    @property
    def private_dir(self) -> Path:
        return self.site_dir / "private"

    @property
    def containers_dir(self) -> Path:
        return self.site_dir / "containers"

    @property
    def site_nix(self) -> Path:
        return self.site_dir / "site.nix"

    @property
    def flake_lock(self) -> Path:
        return self.site_dir / "flake.lock"

    @property
    def shared_store(self) -> Path:
        return self.private_dir / "shared" / "secrets.proxnix.json"

    @property
    def host_relay_identity_store(self) -> Path:
        return self.private_dir / "host_relay_identity.age"

    @property
    def relay_cache_dir(self) -> Path:
        return self.private_dir / ".relay-cache"

    @property
    def relay_cache_shared_identity(self) -> Path:
        return self.relay_cache_dir / "shared_age_identity.age"

    @property
    def shared_identity_store(self) -> Path:
        return self.private_dir / "shared_age_identity.age"

    def container_dir(self, vmid: str) -> Path:
        return self.containers_dir / vmid

    def container_store(self, vmid: str) -> Path:
        return self.private_dir / "containers" / vmid / "secrets.proxnix.json"

    def container_identity_store(self, vmid: str) -> Path:
        return self.private_dir / "containers" / vmid / "age_identity.age"

    def relay_cache_container_identity(self, vmid: str) -> Path:
        return self.relay_cache_dir / "containers" / vmid / "age_identity.age"

    def group_store(self, group: str) -> Path:
        return self.private_dir / "groups" / group / "secrets.proxnix.json"

    def container_secret_groups_file(self, vmid: str) -> Path:
        return self.container_dir(vmid) / "secret-groups.list"
