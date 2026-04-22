from __future__ import annotations

import json
import shlex
import sys

from .config import WorkstationConfig
from .errors import ConfigError, ProxnixWorkstationError
from .paths import SitePaths
from .runtime import ensure_commands, run_command
from .secret_provider_embedded import (
    EmbeddedSopsProvider,
    container_recipients,
    create_identity_store,
    ensure_container_identity,
    ensure_host_relay_identity,
    ensure_shared_identity,
    group_recipients,
    read_sops_store_map,
    reencrypt_local,
    shared_recipients,
    sops_get_local,
    sops_set_local,
    sops_unset_local,
)
from .secret_provider_types import (
    SecretProvider,
    SecretScopeRef,
    container_scope,
    group_scope,
    shared_scope,
)


class ExecSecretProvider(SecretProvider):
    name = "exec"

    def __init__(self, command: list[str]) -> None:
        if not command:
            raise ConfigError("exec secret provider requires a command")
        self.command = command
        self._capabilities: set[str] | None = None

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
        if self.supports("list"):
            payload = self._invoke("list", ref=ref)
            names = payload.get("names")
            if not isinstance(names, list) or any(not isinstance(item, str) for item in names):
                raise ProxnixWorkstationError("secret provider returned invalid list payload")
            return sorted(set(names))
        return sorted(self.export_scope(ref))

    def get(self, ref: SecretScopeRef, name: str) -> str | None:
        if self.supports("get"):
            payload = self._invoke("get", ref=ref, name=name)
            if payload.get("found") is False:
                return None
            value = payload.get("value")
            if value is None:
                return None
            if not isinstance(value, str):
                raise ProxnixWorkstationError("secret provider returned invalid get payload")
            return value
        return self.export_scope(ref).get(name)

    def set(self, ref: SecretScopeRef, name: str, value: str) -> None:
        if not self.supports("set"):
            raise ProxnixWorkstationError("configured secret provider does not support set")
        self._invoke("set", ref=ref, name=name, input_text=value)

    def remove(self, ref: SecretScopeRef, name: str) -> None:
        if not self.supports("remove"):
            raise ProxnixWorkstationError("configured secret provider does not support remove")
        self._invoke("remove", ref=ref, name=name)

    def export_scope(self, ref: SecretScopeRef) -> dict[str, str]:
        if self.supports("export-scope"):
            payload = self._invoke("export-scope", ref=ref)
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ProxnixWorkstationError("secret provider returned invalid export-scope payload")
            return {str(key): value for key, value in data.items() if isinstance(value, str)}

        if not self.supports("list") or not self.supports("get"):
            raise ProxnixWorkstationError(
                "configured secret provider must support export-scope or both list and get"
            )

        data: dict[str, str] = {}
        for name in self.list_names(ref):
            value = self.get(ref, name)
            if value is not None:
                data[name] = value
        return data

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
        completed = run_command(args, input_text=input_text)
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
        return ExecSecretProvider(command)
    if provider_name == "exec":
        if not config.secret_provider_command:
            raise ConfigError("PROXNIX_SECRET_PROVIDER=exec requires PROXNIX_SECRET_PROVIDER_COMMAND")
        command = shlex.split(config.secret_provider_command)
        ensure_commands([command[0]])
        return ExecSecretProvider(command)
    if provider_name in {
        "pass",
        "gopass",
        "passhole",
        "pykeepass",
        "keepassxc",
        "keepassxc-cli",
        "bws",
        "bitwarden-secrets",
        "vault",
        "vault-kv",
        "op",
        "1password",
        "onepassword",
        "infisical",
    }:
        return ExecSecretProvider(named_provider_command(provider_name))
    raise ConfigError(f"unsupported secret provider: {provider_name}")


__all__ = [
    "EmbeddedSopsProvider",
    "ExecSecretProvider",
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
