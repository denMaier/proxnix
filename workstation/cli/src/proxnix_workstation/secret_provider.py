from __future__ import annotations

import os
import json
import shlex
import sys
from contextlib import contextmanager


from .config import WorkstationConfig
from .errors import ConfigError, ProxnixWorkstationError
from .paths import SitePaths
from .provider_keys import (
    ensure_container_identity as ensure_provider_container_identity,
    ensure_host_relay_identity as ensure_provider_host_relay_identity,
)
from .runtime import command_env, ensure_commands, run_command
from .secret_provider_embedded import (
    EmbeddedSopsProvider,
    container_recipients,
    create_identity_store,
    ensure_shared_identity,
    group_recipients,
    read_sops_store_map,
    reencrypt_local,
    shared_recipients,
    sops_get_local,
    sops_set_local,
    sops_unset_local,
)
from .secret_provider_adapters import create_named_adapter
from .secret_provider_types import (
    SecretProvider,
    SecretScopeRef,
    container_scope,
    group_scope,
    shared_scope,
)


_MISSING = object()


class NamedSecretProvider(SecretProvider):
    def __init__(self, name: str, *, extra_env: dict[str, str] | None = None) -> None:
        self.adapter = create_named_adapter(name)
        self.name = self.adapter.name
        self.extra_env = dict(extra_env or {})
        self._capabilities = set(self.adapter.capabilities())
        self._scope_exports: dict[SecretScopeRef, dict[str, str]] = {}
        self._scope_name_lists: dict[SecretScopeRef, list[str]] = {}
        self._values: dict[tuple[SecretScopeRef, str], object] = {}

    def describe(self) -> str:
        return self.adapter.name

    def supports(self, capability: str) -> bool:
        return capability in self._capabilities

    def capabilities(self) -> set[str]:
        return set(self._capabilities)

    @contextmanager
    def _environment(self):
        original: dict[str, str | None] = {}
        for key, value in self.extra_env.items():
            original[key] = os.environ.get(key)
            os.environ[key] = value
        try:
            yield
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def _cache_export(self, ref: SecretScopeRef, data: dict[str, str]) -> dict[str, str]:
        cached = dict(data)
        self._scope_exports[ref] = cached
        self._scope_name_lists[ref] = sorted(cached)
        for key, value in cached.items():
            self._values[(ref, key)] = value
        return dict(cached)

    def _invalidate_scope(self, ref: SecretScopeRef) -> None:
        self._scope_exports.pop(ref, None)
        self._scope_name_lists.pop(ref, None)
        for cache_key in [item for item in self._values if item[0] == ref]:
            self._values.pop(cache_key, None)

    def has_any(self, ref: SecretScopeRef) -> bool:
        if ref in self._scope_exports:
            return bool(self._scope_exports[ref])
        if ref in self._scope_name_lists:
            return bool(self._scope_name_lists[ref])
        return bool(self.list_names(ref))

    def list_names(self, ref: SecretScopeRef) -> list[str]:
        if ref in self._scope_name_lists:
            return list(self._scope_name_lists[ref])
        if ref in self._scope_exports:
            names = sorted(self._scope_exports[ref])
            self._scope_name_lists[ref] = names
            return list(names)
        with self._environment():
            names = sorted(set(self.adapter.list(scope=ref.scope, vmid=ref.vmid, group=ref.group)))
        self._scope_name_lists[ref] = names
        return list(names)

    def get(self, ref: SecretScopeRef, name: str) -> str | None:
        cache_key = (ref, name)
        if cache_key in self._values:
            cached = self._values[cache_key]
            return None if cached is _MISSING else str(cached)
        if ref in self._scope_exports:
            value = self._scope_exports[ref].get(name)
            self._values[cache_key] = _MISSING if value is None else value
            return value
        with self._environment():
            value = self.adapter.get(scope=ref.scope, vmid=ref.vmid, group=ref.group, name=name)
        self._values[cache_key] = _MISSING if value is None else value
        return value

    def set(self, ref: SecretScopeRef, name: str, value: str) -> None:
        with self._environment():
            self.adapter.set(scope=ref.scope, vmid=ref.vmid, group=ref.group, name=name, value=value)
        self._invalidate_scope(ref)

    def remove(self, ref: SecretScopeRef, name: str) -> None:
        with self._environment():
            self.adapter.remove(scope=ref.scope, vmid=ref.vmid, group=ref.group, name=name)
        self._invalidate_scope(ref)

    def export_scope(self, ref: SecretScopeRef) -> dict[str, str]:
        if ref in self._scope_exports:
            return dict(self._scope_exports[ref])
        with self._environment():
            data = self.adapter.export_scope(scope=ref.scope, vmid=ref.vmid, group=ref.group)
        return self._cache_export(ref, data)


class ExecSecretProvider(SecretProvider):
    name = "exec"

    def __init__(self, command: list[str], *, extra_env: dict[str, str] | None = None) -> None:
        if not command:
            raise ConfigError("exec secret provider requires a command")
        self.command = command
        self.extra_env = dict(extra_env or {})
        self._capabilities: set[str] | None = None
        self._scope_exports: dict[SecretScopeRef, dict[str, str]] = {}
        self._scope_name_lists: dict[SecretScopeRef, list[str]] = {}
        self._values: dict[tuple[SecretScopeRef, str], object] = {}

    def describe(self) -> str:
        return f"exec:{shlex.join(self.command)}"

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities()

    def capabilities(self) -> set[str]:
        if self._capabilities is None:
            payload = self._invoke("capabilities")
            values = payload.get("capabilities")
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise ProxnixWorkstationError("secret provider returned invalid capabilities payload")
            self._capabilities = set(values)
        return self._capabilities

    def list_names(self, ref: SecretScopeRef) -> list[str]:
        if ref in self._scope_name_lists:
            return list(self._scope_name_lists[ref])
        if ref in self._scope_exports:
            names = sorted(self._scope_exports[ref])
            self._scope_name_lists[ref] = names
            return list(names)
        if self.supports("list"):
            payload = self._invoke("list", ref=ref)
            names = payload.get("names")
            if not isinstance(names, list) or any(not isinstance(item, str) for item in names):
                raise ProxnixWorkstationError("secret provider returned invalid list payload")
            normalized = sorted(set(names))
            self._scope_name_lists[ref] = normalized
            return list(normalized)
        return sorted(self.export_scope(ref))

    def get(self, ref: SecretScopeRef, name: str) -> str | None:
        cache_key = (ref, name)
        if cache_key in self._values:
            cached = self._values[cache_key]
            return None if cached is _MISSING else str(cached)
        if ref in self._scope_exports:
            value = self._scope_exports[ref].get(name)
            self._values[cache_key] = _MISSING if value is None else value
            return value
        if self.supports("get"):
            payload = self._invoke("get", ref=ref, name=name)
            if payload.get("found") is False:
                self._values[cache_key] = _MISSING
                return None
            value = payload.get("value")
            if value is None:
                self._values[cache_key] = _MISSING
                return None
            if not isinstance(value, str):
                raise ProxnixWorkstationError("secret provider returned invalid get payload")
            self._values[cache_key] = value
            return value
        return self.export_scope(ref).get(name)

    def set(self, ref: SecretScopeRef, name: str, value: str) -> None:
        if not self.supports("set"):
            raise ProxnixWorkstationError("configured secret provider does not support set")
        self._invoke("set", ref=ref, name=name, input_text=value)
        self._invalidate_scope(ref)

    def remove(self, ref: SecretScopeRef, name: str) -> None:
        if not self.supports("remove"):
            raise ProxnixWorkstationError("configured secret provider does not support remove")
        self._invoke("remove", ref=ref, name=name)
        self._invalidate_scope(ref)

    def export_scope(self, ref: SecretScopeRef) -> dict[str, str]:
        if ref in self._scope_exports:
            return dict(self._scope_exports[ref])
        if self.supports("export-scope"):
            payload = self._invoke("export-scope", ref=ref)
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ProxnixWorkstationError("secret provider returned invalid export-scope payload")
            return self._cache_export(
                ref, {str(key): value for key, value in data.items() if isinstance(value, str)}
            )

        if not self.supports("list") or not self.supports("get"):
            raise ProxnixWorkstationError(
                "configured secret provider must support export-scope or both list and get"
            )

        data: dict[str, str] = {}
        for name in self.list_names(ref):
            value = self.get(ref, name)
            if value is not None:
                data[name] = value
        return self._cache_export(ref, data)

    def has_any(self, ref: SecretScopeRef) -> bool:
        if ref in self._scope_exports:
            return bool(self._scope_exports[ref])
        if ref in self._scope_name_lists:
            return bool(self._scope_name_lists[ref])
        if self.supports("list"):
            return bool(self.list_names(ref))
        return bool(self.export_scope(ref))

    def _cache_export(self, ref: SecretScopeRef, data: dict[str, str]) -> dict[str, str]:
        cached = dict(data)
        self._scope_exports[ref] = cached
        self._scope_name_lists[ref] = sorted(cached)
        for key, value in cached.items():
            self._values[(ref, key)] = value
        return dict(cached)

    def _invalidate_scope(self, ref: SecretScopeRef) -> None:
        self._scope_exports.pop(ref, None)
        self._scope_name_lists.pop(ref, None)
        for cache_key in [item for item in self._values if item[0] == ref]:
            self._values.pop(cache_key, None)

    def _invoke(
        self,
        operation: str,
        *,
        ref: SecretScopeRef | None = None,
        name: str | None = None,
        input_text: str | None = None,
    ) -> dict[str, object]:
        args = [*self.command, operation]
        if ref is not None:
            args.extend(ref.cli_args())
        if name is not None:
            args.extend(["--name", name])
        completed = run_command(
            args,
            input_text=input_text,
            env=command_env(self.extra_env) if self.extra_env else None,
        )
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ProxnixWorkstationError(
                f"secret provider returned invalid JSON for {operation}: {self.describe()}"
            ) from exc
        if not isinstance(payload, dict):
            raise ProxnixWorkstationError(
                f"secret provider returned invalid JSON object for {operation}: {self.describe()}"
            )
        if payload.get("ok") is False:
            detail = payload.get("error")
            suffix = f": {detail}" if isinstance(detail, str) and detail else ""
            raise ProxnixWorkstationError(f"secret provider {operation} failed{suffix}")
        return payload


def named_provider_command(name: str) -> list[str]:
    return [sys.executable, "-m", "proxnix_workstation.secret_provider_adapters", name]


def load_secret_provider(
    config: WorkstationConfig,
    site_paths: SitePaths | None = None,
) -> SecretProvider:
    provider_name = config.secret_provider.strip() or "embedded-sops"
    if provider_name == "embedded-sops":
        paths = SitePaths.from_config(config) if site_paths is None else site_paths
        return EmbeddedSopsProvider(config, paths)
    if provider_name.startswith("exec:"):
        command_text = provider_name.split(":", 1)[1].strip()
        if not command_text:
            raise ConfigError("exec secret provider requires a command after exec:")
        command = shlex.split(command_text)
        ensure_commands([command[0]])
        return ExecSecretProvider(command, extra_env=config.provider_environment_map())
    if provider_name == "exec":
        if not config.secret_provider_command:
            raise ConfigError("PROXNIX_SECRET_PROVIDER=exec requires PROXNIX_SECRET_PROVIDER_COMMAND")
        command = shlex.split(config.secret_provider_command)
        ensure_commands([command[0]])
        return ExecSecretProvider(command, extra_env=config.provider_environment_map())
    if provider_name in {
        "pass",
        "gopass",
        "passhole",
        "pykeepass",
        "keepassxc",
        "onepassword",
        "onepassword-cli",
        "bitwarden",
        "bitwarden-cli",
    }:
        return NamedSecretProvider(provider_name, extra_env=config.provider_environment_map())
    raise ConfigError(f"unsupported secret provider: {provider_name}")


def ensure_host_relay_identity(config: WorkstationConfig, site_paths: SitePaths) -> None:
    provider = load_secret_provider(config, site_paths)
    ensure_provider_host_relay_identity(config, provider, site_paths)


def ensure_container_identity(config: WorkstationConfig, site_paths: SitePaths, vmid: str) -> None:
    provider = load_secret_provider(config, site_paths)
    ensure_provider_container_identity(config, provider, site_paths, vmid)


__all__ = [
    "EmbeddedSopsProvider",
    "ExecSecretProvider",
    "NamedSecretProvider",
    "SecretProvider",
    "SecretScopeRef",
    "container_scope",
    "group_scope",
    "shared_scope",
    "create_identity_store",
    "ensure_host_relay_identity",
    "ensure_container_identity",
    "ensure_shared_identity",
    "container_recipients",
    "shared_recipients",
    "group_recipients",
    "sops_set_local",
    "sops_unset_local",
    "sops_get_local",
    "read_sops_store_map",
    "reencrypt_local",
    "named_provider_command",
    "load_secret_provider",
]
