from __future__ import annotations

import json
from pathlib import Path

from .config import WorkstationConfig
from .errors import ProxnixWorkstationError
from .paths import SitePaths
from .sops_ops import (
    ensure_flat_secret_map,
    ensure_private_permissions,
    generate_identity_keypair,
    identity_public_key_from_store,
    master_recipient,
    sops_decrypt_json,
    sops_encrypt_json_text,
    sops_encrypt_yaml_text,
    write_identity_payload,
)
from .secret_provider_types import SecretProvider, SecretScopeRef


def ensure_secret_parent_dir(site_paths: SitePaths, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_private_permissions(site_paths.private_dir)


def write_store_atomic(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.chmod(mode)
    tmp_path.replace(path)
    path.chmod(mode)


def create_identity_store(config: WorkstationConfig, site_paths: SitePaths, store: Path, label: str) -> tuple[str, str]:
    if store.exists():
        raise ProxnixWorkstationError(f"{label} keypair already exists")

    ensure_secret_parent_dir(site_paths, store)
    private_text, pubkey = generate_identity_keypair()
    payload = write_identity_payload(private_text.rstrip("\n"))
    encrypted = sops_encrypt_yaml_text(config, payload, master_recipient(config))
    write_store_atomic(store, encrypted)
    return label, pubkey


def ensure_host_relay_identity(config: WorkstationConfig, site_paths: SitePaths) -> None:
    store = site_paths.host_relay_identity_store
    if not store.exists():
        create_identity_store(config, site_paths, store, "host relay")


def ensure_container_identity(config: WorkstationConfig, site_paths: SitePaths, vmid: str) -> None:
    store = site_paths.container_identity_store(vmid)
    if not store.exists():
        create_identity_store(config, site_paths, store, f"container {vmid}")


def ensure_shared_identity(config: WorkstationConfig, site_paths: SitePaths) -> None:
    store = site_paths.shared_identity_store
    if not store.exists():
        create_identity_store(config, site_paths, store, "shared")


def container_recipients(config: WorkstationConfig, site_paths: SitePaths, vmid: str) -> str:
    ensure_container_identity(config, site_paths, vmid)
    return ",".join(
        [
            identity_public_key_from_store(config, site_paths.container_identity_store(vmid)),
            master_recipient(config),
        ]
    )


def shared_recipients(config: WorkstationConfig, site_paths: SitePaths) -> str:
    ensure_shared_identity(config, site_paths)
    return ",".join(
        [
            identity_public_key_from_store(config, site_paths.shared_identity_store),
            master_recipient(config),
        ]
    )


def group_recipients(config: WorkstationConfig, site_paths: SitePaths) -> str:
    return shared_recipients(config, site_paths)


def ensure_sops_store(config: WorkstationConfig, site_paths: SitePaths, path: Path, recipients: str) -> None:
    if path.exists():
        return
    ensure_secret_parent_dir(site_paths, path)
    encrypted = sops_encrypt_json_text(config, "{}\n", recipients)
    write_store_atomic(path, encrypted)


def read_sops_store_map(config: WorkstationConfig, path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    return ensure_flat_secret_map(sops_decrypt_json(config, path), source=str(path))


def write_sops_store_map(
    config: WorkstationConfig,
    path: Path,
    recipients: str,
    data: dict[str, str],
) -> None:
    plaintext = json.dumps(data, indent=2, sort_keys=True) + "\n"
    encrypted = sops_encrypt_json_text(config, plaintext, recipients)
    write_store_atomic(path, encrypted)


def sops_set_local(
    config: WorkstationConfig,
    site_paths: SitePaths,
    path: Path,
    recipients: str,
    name: str,
    value: str,
) -> None:
    ensure_sops_store(config, site_paths, path, recipients)
    data = read_sops_store_map(config, path) or {}
    data[name] = value
    write_sops_store_map(config, path, recipients, data)


def sops_unset_local(config: WorkstationConfig, path: Path, recipients: str, name: str) -> None:
    if not path.exists():
        return
    data = read_sops_store_map(config, path) or {}
    if name not in data:
        return
    del data[name]
    write_sops_store_map(config, path, recipients, data)


def sops_get_local(config: WorkstationConfig, path: Path, name: str) -> str | None:
    data = read_sops_store_map(config, path)
    if data is None:
        return None
    return data.get(name)


def reencrypt_local(config: WorkstationConfig, path: Path, recipients: str) -> None:
    if not path.exists():
        raise ProxnixWorkstationError(f"store not found: {path}")
    data = read_sops_store_map(config, path) or {}
    write_sops_store_map(config, path, recipients, data)


class EmbeddedSopsProvider(SecretProvider):
    name = "embedded-sops"

    def __init__(self, config: WorkstationConfig, site_paths: SitePaths) -> None:
        self.config = config
        self.site_paths = site_paths

    def supports(self, capability: str) -> bool:
        return capability in {"list", "get", "set", "remove", "export-scope", "rotate"}

    def describe(self) -> str:
        return "embedded-sops"

    def list_names(self, ref: SecretScopeRef) -> list[str]:
        return sorted(self.export_scope(ref))

    def get(self, ref: SecretScopeRef, name: str) -> str | None:
        return self.export_scope(ref).get(name)

    def set(self, ref: SecretScopeRef, name: str, value: str) -> None:
        path = self._path_for(ref)
        recipients = self._recipients_for(ref)
        sops_set_local(self.config, self.site_paths, path, recipients, name, value)

    def remove(self, ref: SecretScopeRef, name: str) -> None:
        path = self._path_for(ref)
        recipients = self._recipients_for(ref)
        sops_unset_local(self.config, path, recipients, name)

    def rotate_scope(self, ref: SecretScopeRef) -> None:
        path = self._path_for(ref)
        recipients = self._recipients_for(ref)
        reencrypt_local(self.config, path, recipients)

    def export_scope(self, ref: SecretScopeRef) -> dict[str, str]:
        path = self._path_for(ref)
        return read_sops_store_map(self.config, path) or {}

    def _path_for(self, ref: SecretScopeRef) -> Path:
        if ref.scope == "shared":
            return self.site_paths.shared_store
        if ref.scope == "group":
            if ref.group is None:
                raise ProxnixWorkstationError("group scope requires group name")
            return self.site_paths.group_store(ref.group)
        if ref.scope == "container":
            if ref.vmid is None:
                raise ProxnixWorkstationError("container scope requires VMID")
            return self.site_paths.container_store(ref.vmid)
        raise ProxnixWorkstationError(f"unsupported secret scope: {ref.scope}")

    def _recipients_for(self, ref: SecretScopeRef) -> str:
        if ref.scope == "shared":
            return shared_recipients(self.config, self.site_paths)
        if ref.scope == "group":
            return group_recipients(self.config, self.site_paths)
        if ref.scope == "container":
            if ref.vmid is None:
                raise ProxnixWorkstationError("container scope requires VMID")
            return container_recipients(self.config, self.site_paths, ref.vmid)
        raise ProxnixWorkstationError(f"unsupported secret scope: {ref.scope}")
