from __future__ import annotations

from pathlib import Path

from .config import WorkstationConfig
from .errors import ConfigError, ProxnixWorkstationError
from .paths import SitePaths
from .secret_provider_embedded import (
    EmbeddedSopsProvider,
    create_identity_store,
    ensure_container_identity as ensure_embedded_container_identity,
    ensure_host_relay_identity as ensure_embedded_host_relay_identity,
)
from .secret_provider_types import SecretProvider, group_scope
from .sops_ops import decrypt_identity_text, generate_identity_keypair, public_key_from_private_text


INTERNAL_KEYS_GROUP = "_proxnix_keys"
MASTER_KEY_NAME = "master"
HOST_RELAY_KEY_NAME = "host-relay"


def container_key_name(vmid: str) -> str:
    return f"container-{vmid}"


def _internal_keys_ref():
    return group_scope(INTERNAL_KEYS_GROUP)


def _is_embedded_provider(provider: SecretProvider) -> bool:
    return isinstance(provider, EmbeddedSopsProvider)


def _provider_get_key(provider: SecretProvider, name: str) -> str | None:
    if not hasattr(provider, "get"):
        return None
    return provider.get(_internal_keys_ref(), name)


def _provider_set_key(provider: SecretProvider, name: str, value: str) -> None:
    if not hasattr(provider, "set"):
        raise ProxnixWorkstationError("configured secret provider does not support proxnix key storage")
    provider.set(_internal_keys_ref(), name, value)


def sops_master_identity_path(config: WorkstationConfig) -> Path:
    raw = config.provider_environment_map().get("PROXNIX_SOPS_MASTER_IDENTITY", "").strip()
    if not raw:
        raise ConfigError("PROXNIX_SOPS_MASTER_IDENTITY is not configured")
    return Path(raw).expanduser()


def master_private_key_text(config: WorkstationConfig, provider: SecretProvider) -> str:
    if not _is_embedded_provider(provider):
        value = _provider_get_key(provider, MASTER_KEY_NAME)
        if value is not None:
            return value
    identity_path = sops_master_identity_path(config)
    if not identity_path.is_file():
        raise ConfigError(f"SOPS master SSH identity not found: {identity_path}")
    return identity_path.read_text(encoding="utf-8")


def host_relay_private_key_text(
    config: WorkstationConfig,
    provider: SecretProvider,
    site_paths: SitePaths,
) -> str | None:
    if _is_embedded_provider(provider):
        store = site_paths.host_relay_identity_store
        if not store.is_file():
            return None
        return decrypt_identity_text(config, store)
    return _provider_get_key(provider, HOST_RELAY_KEY_NAME)


def container_private_key_text(
    config: WorkstationConfig,
    provider: SecretProvider,
    site_paths: SitePaths,
    vmid: str,
) -> str | None:
    if _is_embedded_provider(provider):
        store = site_paths.container_identity_store(vmid)
        if not store.is_file():
            return None
        return decrypt_identity_text(config, store)
    return _provider_get_key(provider, container_key_name(vmid))


def have_host_relay_private_key(
    config: WorkstationConfig,
    provider: SecretProvider,
    site_paths: SitePaths,
) -> bool:
    return host_relay_private_key_text(config, provider, site_paths) is not None


def have_container_private_key(
    config: WorkstationConfig,
    provider: SecretProvider,
    site_paths: SitePaths,
    vmid: str,
) -> bool:
    return container_private_key_text(config, provider, site_paths, vmid) is not None


def host_relay_public_key(
    config: WorkstationConfig,
    provider: SecretProvider,
    site_paths: SitePaths,
) -> str:
    private_text = host_relay_private_key_text(config, provider, site_paths)
    if private_text is None:
        raise ProxnixWorkstationError("host relay identity is missing")
    return public_key_from_private_text(private_text, source=HOST_RELAY_KEY_NAME)


def master_public_key(config: WorkstationConfig, provider: SecretProvider) -> str:
    return public_key_from_private_text(master_private_key_text(config, provider), source=MASTER_KEY_NAME)


def container_public_key(
    config: WorkstationConfig,
    provider: SecretProvider,
    site_paths: SitePaths,
    vmid: str,
) -> str:
    private_text = container_private_key_text(config, provider, site_paths, vmid)
    if private_text is None:
        raise ProxnixWorkstationError(f"container {vmid} identity is missing")
    return public_key_from_private_text(private_text, source=container_key_name(vmid))


def initialize_host_relay_identity(
    config: WorkstationConfig,
    provider: SecretProvider,
    site_paths: SitePaths,
) -> tuple[str, str]:
    if _is_embedded_provider(provider):
        return create_identity_store(config, site_paths, site_paths.host_relay_identity_store, "host relay")
    if have_host_relay_private_key(config, provider, site_paths):
        raise ProxnixWorkstationError("host relay keypair already exists")
    private_text, pubkey = generate_identity_keypair()
    _provider_set_key(provider, HOST_RELAY_KEY_NAME, private_text)
    return "host relay", pubkey


def initialize_container_identity(
    config: WorkstationConfig,
    provider: SecretProvider,
    site_paths: SitePaths,
    vmid: str,
) -> tuple[str, str]:
    if _is_embedded_provider(provider):
        return create_identity_store(config, site_paths, site_paths.container_identity_store(vmid), f"container {vmid}")
    if have_container_private_key(config, provider, site_paths, vmid):
        raise ProxnixWorkstationError(f"container {vmid} keypair already exists")
    private_text, pubkey = generate_identity_keypair()
    _provider_set_key(provider, container_key_name(vmid), private_text)
    return f"container {vmid}", pubkey


def ensure_host_relay_identity(
    config: WorkstationConfig,
    provider: SecretProvider,
    site_paths: SitePaths,
) -> None:
    if _is_embedded_provider(provider):
        ensure_embedded_host_relay_identity(config, site_paths)
        return
    if have_host_relay_private_key(config, provider, site_paths):
        return
    private_text, _ = generate_identity_keypair()
    _provider_set_key(provider, HOST_RELAY_KEY_NAME, private_text)


def ensure_container_identity(
    config: WorkstationConfig,
    provider: SecretProvider,
    site_paths: SitePaths,
    vmid: str,
) -> None:
    if _is_embedded_provider(provider):
        ensure_embedded_container_identity(config, site_paths, vmid)
        return
    if have_container_private_key(config, provider, site_paths, vmid):
        return
    private_text, _ = generate_identity_keypair()
    _provider_set_key(provider, container_key_name(vmid), private_text)


def write_private_key_file(private_text: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(private_text, encoding="utf-8")
    destination.chmod(0o600)
