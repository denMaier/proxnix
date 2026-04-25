from __future__ import annotations

import argparse
import contextlib
import io
import os
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path

from .config import WorkstationConfig, load_workstation_config
from .errors import ConfigError, PlanningError, ProxnixWorkstationError
from .json_api import error as json_error
from .json_api import ok as json_ok
from .json_api import print_json
from .manager_api import build_status
from .keepass_agent import derive_pykeepass_agent_password
from .paths import SitePaths
from .provider_keys import have_container_private_key, initialize_container_identity, initialize_host_relay_identity
from .runtime import ensure_commands
from .site import collect_site_vmids
from .secret_provider import (
    EmbeddedSopsProvider,
    container_recipients,
    container_scope,
    create_identity_store,
    ensure_container_identity,
    ensure_host_relay_identity,
    group_scope,
    group_recipients,
    load_secret_provider,
    shared_recipients,
    shared_scope,
    sops_set_local,
)
from .site import read_container_secret_groups, valid_secret_group_name
from .sops_ops import read_secret_value


@dataclass(frozen=True)
class SecretScope:
    scope_type: str
    scope_id: str

    @property
    def ref(self):
        if self.scope_type == "shared":
            return shared_scope()
        if self.scope_type == "group":
            return group_scope(self.scope_id)
        if self.scope_type == "container":
            return container_scope(self.scope_id)
        raise ProxnixWorkstationError(f"unsupported secret scope: {self.scope_type}")


@contextmanager
def _provider_environment(provider_env: dict[str, str]):
    original: dict[str, str | None] = {}
    for key, value in provider_env.items():
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


def need_tools(config: WorkstationConfig) -> SitePaths:
    site_paths = SitePaths.from_config(config)
    ensure_commands(["sops"])
    return site_paths


def _command_result(output: str, exit_code: int, *, error: str = "") -> dict[str, object]:
    return {
        "output": output,
        "exitCode": exit_code,
        "error": error if exit_code != 0 else "",
    }


def _scope_from_args(scope_type: str, scope_id: str | None = None) -> SecretScope:
    scope_id = (scope_id or "").strip()
    if scope_type == "shared":
        return SecretScope("shared", "")
    if scope_type == "group":
        if not valid_secret_group_name(scope_id):
            raise ProxnixWorkstationError(f"invalid group name: {scope_id}")
        return SecretScope("group", scope_id)
    if scope_type == "container":
        if not scope_id.isdigit():
            raise ProxnixWorkstationError(f"invalid container VMID: {scope_id}")
        return SecretScope("container", scope_id)
    raise ProxnixWorkstationError("secret scope must be shared, group, or container")


def _entries_for_scope(config: WorkstationConfig, scope: SecretScope) -> list[dict[str, str]]:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    entries: list[dict[str, str]] = []

    if scope.scope_type == "container":
        seen: dict[str, str] = {}
        for key in provider.list_names(shared_scope()):
            seen.setdefault(key, "shared")
        for group in read_container_secret_groups(site_paths, scope.scope_id):
            for key in provider.list_names(group_scope(group)):
                if seen.get(key) != "container":
                    seen[key] = f"group:{group}"
        for key in provider.list_names(container_scope(scope.scope_id)):
            seen[key] = "container"
        return [{"name": key, "source": seen[key]} for key in sorted(seen)]

    for key in provider.list_names(scope.ref):
        entries.append({"name": key, "source": scope.scope_type})
    return entries


def secret_scope_status_data(config: WorkstationConfig, scope: SecretScope) -> dict[str, object]:
    warnings: list[str] = []
    entries: list[dict[str, str]] = []
    if config.site_dir is None:
        warnings.append("Set site directory first.")
    else:
        try:
            entries = _entries_for_scope(config, scope)
        except Exception as exc:
            warnings.append(str(exc))
    return {
        "scopeType": scope.scope_type,
        "scopeId": scope.scope_id,
        "entries": entries,
        "canRotate": config.secret_provider == "embedded-sops",
        "warnings": warnings,
    }


def secrets_provider_status_data(config: WorkstationConfig) -> dict[str, object]:
    warnings: list[str] = []
    defined_groups: list[str] = []
    container_identities: dict[str, bool] = {}

    try:
        status = build_status(config.config_file)
        for warning in status.get("warnings", []):
            warnings.append(str(warning))
        containers = status.get("containers", [])
        if isinstance(containers, list):
            for container in containers:
                if isinstance(container, dict):
                    vmid = str(container.get("vmid", "")).strip()
                    if vmid:
                        container_identities[vmid] = False
        local_defined = status.get("definedSecretGroups", [])
        if isinstance(local_defined, list):
            defined_groups = [str(group) for group in local_defined]
    except Exception as exc:
        warnings.append(str(exc))

    if config.site_dir is None or not config.site_dir.is_dir():
        return {
            "provider": config.secret_provider,
            "definedSecretGroups": defined_groups,
            "containerIdentities": container_identities,
            "warnings": warnings,
        }

    try:
        site_paths = SitePaths.from_config(config)
        provider = load_secret_provider(config, site_paths)
        attached_groups = set()
        for vmid in container_identities:
            try:
                container_identities[vmid] = have_container_private_key(config, provider, site_paths, vmid)
                attached_groups.update(read_container_secret_groups(site_paths, vmid))
            except Exception as exc:
                warnings.append(f"Could not check container {vmid}: {exc}")
        if isinstance(provider, EmbeddedSopsProvider):
            defined_groups = sorted(
                {
                    *defined_groups,
                    *[
                        group
                        for group in attached_groups
                        if site_paths.group_store(group).is_file() or (site_paths.private_dir / "groups" / group).is_dir()
                    ],
                }
            )
        else:
            provider_groups = []
            for group in sorted(attached_groups):
                try:
                    if provider.has_any(group_scope(group)):
                        provider_groups.append(group)
                except Exception:
                    pass
            defined_groups = sorted({*defined_groups, *provider_groups})
    except Exception as exc:
        warnings.append(f"Secret backend unavailable: {exc}")

    return {
        "provider": config.secret_provider,
        "definedSecretGroups": defined_groups,
        "containerIdentities": container_identities,
        "warnings": warnings,
    }


def cmd_status(config: WorkstationConfig, *, json: bool) -> int:
    data = secrets_provider_status_data(config)
    if json:
        print_json(json_ok(data, warnings=[str(w) for w in data["warnings"]]))
    else:
        print(f"provider\t{data['provider']}")
        for warning in data["warnings"]:
            print(f"warning\t{warning}")
    return 0


def cmd_scope_status(config: WorkstationConfig, scope: SecretScope, *, json: bool) -> int:
    data = secret_scope_status_data(config, scope)
    if json:
        print_json(json_ok(data, warnings=[str(w) for w in data["warnings"]]))
    else:
        for entry in data["entries"]:
            print(f"{entry['name']}\t{entry['source']}")
        for warning in data["warnings"]:
            print(f"warning\t{warning}")
    return 0


def cmd_ls(config: WorkstationConfig, vmid: str | None) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    if vmid is None:
        rows: list[tuple[str, str]] = []
        for key in provider.list_names(shared_scope()):
            rows.append(("shared", key))
        known_groups: set[str] = set()
        for known_vmid in collect_site_vmids(site_paths):
            for key in provider.list_names(container_scope(known_vmid)):
                rows.append((known_vmid, key))
            known_groups.update(read_container_secret_groups(site_paths, known_vmid))
        for group in sorted(known_groups):
            for key in provider.list_names(group_scope(group)):
                rows.append((f"group:{group}", key))
        for prefix, key in sorted(rows):
            print(f"{prefix}\t{key}")
        return 0

    seen: dict[str, str] = {}
    for key in provider.list_names(shared_scope()):
        seen.setdefault(key, "shared")
    for group in read_container_secret_groups(site_paths, vmid):
        for key in provider.list_names(group_scope(group)):
            if seen.get(key) != "container":
                seen[key] = f"group:{group}"
    for key in provider.list_names(container_scope(vmid)):
        seen[key] = "container"
    for key in sorted(seen):
        print(f"{key}\t{seen[key]}")
    return 0


def cmd_ls_shared(config: WorkstationConfig) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    for key in provider.list_names(shared_scope()):
        print(key)
    return 0


def cmd_ls_group(config: WorkstationConfig, group: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    if not valid_secret_group_name(group):
        raise ProxnixWorkstationError(f"invalid group name: {group}")
    for key in provider.list_names(group_scope(group)):
        print(key)
    return 0


def cmd_get(config: WorkstationConfig, vmid: str, name: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    container_data = provider.export_scope(container_scope(vmid))
    if name in container_data:
        print(container_data[name])
        return 0

    matched_group: str | None = None
    for group in read_container_secret_groups(site_paths, vmid):
        if name in provider.export_scope(group_scope(group)):
            if matched_group is not None:
                raise ProxnixWorkstationError(
                    f"secret {name} is ambiguous for vmid={vmid}: found in groups {matched_group} and {group}"
                )
            matched_group = group

    if matched_group is not None:
        print(provider.export_scope(group_scope(matched_group))[name])
        return 0

    shared_data = provider.export_scope(shared_scope())
    if name not in shared_data:
        raise ProxnixWorkstationError(f"secret not found for vmid={vmid}: {name}")
    print(shared_data[name])
    return 0


def cmd_get_shared(config: WorkstationConfig, name: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    value = provider.get(shared_scope(), name)
    if value is None:
        raise ProxnixWorkstationError(f"shared secret not found: {name}")
    print(value)
    return 0


def cmd_get_group(config: WorkstationConfig, group: str, name: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    if not valid_secret_group_name(group):
        raise ProxnixWorkstationError(f"invalid group name: {group}")
    value = provider.get(group_scope(group), name)
    if value is None:
        raise ProxnixWorkstationError(f"group secret not found: group={group} name={name}")
    print(value)
    return 0


def cmd_set(config: WorkstationConfig, vmid: str, name: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    value = read_secret_value()
    provider.set(container_scope(vmid), name, value)
    print(f"Set: vmid={vmid} name={name}")
    return 0


def cmd_set_shared(config: WorkstationConfig, name: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    value = read_secret_value()
    provider.set(shared_scope(), name, value)
    print(f"Set shared: name={name}")
    return 0


def cmd_set_group(config: WorkstationConfig, group: str, name: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    if not valid_secret_group_name(group):
        raise ProxnixWorkstationError(f"invalid group name: {group}")
    value = read_secret_value()
    provider.set(group_scope(group), name, value)
    print(f"Set group: group={group} name={name}")
    return 0


def cmd_rm(config: WorkstationConfig, vmid: str, name: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    provider.remove(container_scope(vmid), name)
    print(f"Removed: vmid={vmid} name={name}")
    return 0


def cmd_rm_shared(config: WorkstationConfig, name: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    provider.remove(shared_scope(), name)
    print(f"Removed shared: name={name}")
    return 0


def cmd_rm_group(config: WorkstationConfig, group: str, name: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    if not valid_secret_group_name(group):
        raise ProxnixWorkstationError(f"invalid group name: {group}")
    provider.remove(group_scope(group), name)
    print(f"Removed group secret: group={group} name={name}")
    return 0


def cmd_rotate(config: WorkstationConfig, vmid: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    if not isinstance(provider, EmbeddedSopsProvider):
        raise ProxnixWorkstationError("rotate is only supported by the embedded-sops secret provider")
    provider.rotate_scope(container_scope(vmid))
    print(f"Rotated store: vmid={vmid}")
    return 0


def cmd_rotate_shared(config: WorkstationConfig) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    if not isinstance(provider, EmbeddedSopsProvider):
        raise ProxnixWorkstationError("rotate-shared is only supported by the embedded-sops secret provider")
    provider.rotate_scope(shared_scope())
    print("Rotated shared store")
    return 0


def cmd_rotate_group(config: WorkstationConfig, group: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    if not valid_secret_group_name(group):
        raise ProxnixWorkstationError(f"invalid group name: {group}")
    if not isinstance(provider, EmbeddedSopsProvider):
        raise ProxnixWorkstationError("rotate-group is only supported by the embedded-sops secret provider")
    provider.rotate_scope(group_scope(group))
    print(f"Rotated group store: group={group}")
    return 0


def cmd_init_host_relay(config: WorkstationConfig) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    label, pubkey = initialize_host_relay_identity(config, provider, site_paths)
    print(f"Initialized {label} SSH-backed age keypair")
    print(f"Public key: {pubkey}")
    return 0


def cmd_init_shared(config: WorkstationConfig) -> int:
    site_paths = need_tools(config)
    label, pubkey = create_identity_store(config, site_paths, site_paths.shared_identity_store, "shared")
    print(f"Initialized {label} SSH-backed age keypair")
    print(f"Public key: {pubkey}")
    return 0


def cmd_init_container(config: WorkstationConfig, vmid: str) -> int:
    site_paths = need_tools(config)
    provider = load_secret_provider(config, site_paths)
    label, pubkey = initialize_container_identity(config, provider, site_paths, vmid)
    print(f"Initialized {label} SSH-backed age keypair")
    print(f"Public key: {pubkey}")
    return 0


def cmd_print_keepass_password(config: WorkstationConfig) -> int:
    provider_env = config.provider_environment_map()
    if config.secret_provider != "pykeepass":
        raise ProxnixWorkstationError(
            "print-keepass-password is only supported when PROXNIX_SECRET_PROVIDER=pykeepass"
        )
    database_path = provider_env.get("PROXNIX_PYKEEPASS_DATABASE", "").strip()
    if not database_path:
        raise ProxnixWorkstationError("PROXNIX_PYKEEPASS_DATABASE is not configured")
    public_key = provider_env.get("PROXNIX_PYKEEPASS_AGENT_PUBLIC_KEY", "").strip()
    if not public_key:
        raise ProxnixWorkstationError("PROXNIX_PYKEEPASS_AGENT_PUBLIC_KEY is not configured")
    with _provider_environment(provider_env):
        password = derive_pykeepass_agent_password(database_path, public_key)
    print(password)
    return 0


def _print_command_json(output: str, exit_code: int = 0, error: str = "") -> None:
    print_json(json_ok(_command_result(output, exit_code, error=error)))


def _run_for_json(fn) -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        return fn()


def build_parser(*, prog: str = "proxnix-secrets") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--config", type=Path, help="Path to the proxnix workstation config file")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")

    scope_status = sub.add_parser("scope-status")
    scope_status.add_argument("--scope", choices=["shared", "group", "container"], required=True)
    scope_status.add_argument("--id")
    scope_status.add_argument("--json", action="store_true")

    ls = sub.add_parser("ls")
    ls.add_argument("vmid", nargs="?")
    sub.add_parser("ls-shared")
    lsg = sub.add_parser("ls-group")
    lsg.add_argument("group")

    get = sub.add_parser("get")
    get.add_argument("vmid")
    get.add_argument("name")
    gets = sub.add_parser("get-shared")
    gets.add_argument("name")
    getg = sub.add_parser("get-group")
    getg.add_argument("group")
    getg.add_argument("name")

    setp = sub.add_parser("set")
    setp.add_argument("vmid")
    setp.add_argument("name")
    setp.add_argument("--json", action="store_true")
    sets = sub.add_parser("set-shared")
    sets.add_argument("name")
    sets.add_argument("--json", action="store_true")
    setg = sub.add_parser("set-group")
    setg.add_argument("group")
    setg.add_argument("name")
    setg.add_argument("--json", action="store_true")

    rmp = sub.add_parser("rm")
    rmp.add_argument("vmid")
    rmp.add_argument("name")
    rmp.add_argument("--json", action="store_true")
    rms = sub.add_parser("rm-shared")
    rms.add_argument("name")
    rms.add_argument("--json", action="store_true")
    rmg = sub.add_parser("rm-group")
    rmg.add_argument("group")
    rmg.add_argument("name")
    rmg.add_argument("--json", action="store_true")

    rot = sub.add_parser("rotate")
    rot.add_argument("vmid")
    rot.add_argument("--json", action="store_true")
    rots = sub.add_parser("rotate-shared")
    rots.add_argument("--json", action="store_true")
    rotg = sub.add_parser("rotate-group")
    rotg.add_argument("group")
    rotg.add_argument("--json", action="store_true")

    sub.add_parser("init-host-relay")
    initc = sub.add_parser("init-container")
    initc.add_argument("vmid")
    initc.add_argument("--json", action="store_true")
    sub.add_parser("init-shared")
    sub.add_parser("print-keepass-password")
    return parser


def main(argv: list[str] | None = None, *, prog: str = "proxnix-secrets") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    config = load_workstation_config(args.config)

    try:
        match args.command:
            case "status":
                return cmd_status(config, json=args.json)
            case "scope-status":
                return cmd_scope_status(config, _scope_from_args(args.scope, args.id), json=args.json)
            case "ls":
                return cmd_ls(config, args.vmid)
            case "ls-shared":
                return cmd_ls_shared(config)
            case "ls-group":
                return cmd_ls_group(config, args.group)
            case "get":
                return cmd_get(config, args.vmid, args.name)
            case "get-shared":
                return cmd_get_shared(config, args.name)
            case "get-group":
                return cmd_get_group(config, args.group, args.name)
            case "set":
                if args.json:
                    rc = _run_for_json(lambda: cmd_set(config, args.vmid, args.name))
                    _print_command_json(f"Set secret {args.name}.", rc)
                    return rc
                rc = cmd_set(config, args.vmid, args.name)
                return rc
            case "set-shared":
                if args.json:
                    rc = _run_for_json(lambda: cmd_set_shared(config, args.name))
                    _print_command_json(f"Set secret {args.name}.", rc)
                    return rc
                rc = cmd_set_shared(config, args.name)
                return rc
            case "set-group":
                if args.json:
                    rc = _run_for_json(lambda: cmd_set_group(config, args.group, args.name))
                    _print_command_json(f"Set secret {args.name}.", rc)
                    return rc
                rc = cmd_set_group(config, args.group, args.name)
                return rc
            case "rm":
                if args.json:
                    rc = _run_for_json(lambda: cmd_rm(config, args.vmid, args.name))
                    _print_command_json(f"Removed secret {args.name}.", rc)
                    return rc
                rc = cmd_rm(config, args.vmid, args.name)
                return rc
            case "rm-shared":
                if args.json:
                    rc = _run_for_json(lambda: cmd_rm_shared(config, args.name))
                    _print_command_json(f"Removed secret {args.name}.", rc)
                    return rc
                rc = cmd_rm_shared(config, args.name)
                return rc
            case "rm-group":
                if args.json:
                    rc = _run_for_json(lambda: cmd_rm_group(config, args.group, args.name))
                    _print_command_json(f"Removed secret {args.name}.", rc)
                    return rc
                rc = cmd_rm_group(config, args.group, args.name)
                return rc
            case "rotate":
                if args.json:
                    rc = _run_for_json(lambda: cmd_rotate(config, args.vmid))
                    _print_command_json("Secret store rotated.", rc)
                    return rc
                rc = cmd_rotate(config, args.vmid)
                return rc
            case "rotate-shared":
                if args.json:
                    rc = _run_for_json(lambda: cmd_rotate_shared(config))
                    _print_command_json("Secret store rotated.", rc)
                    return rc
                rc = cmd_rotate_shared(config)
                return rc
            case "rotate-group":
                if args.json:
                    rc = _run_for_json(lambda: cmd_rotate_group(config, args.group))
                    _print_command_json("Secret store rotated.", rc)
                    return rc
                rc = cmd_rotate_group(config, args.group)
                return rc
            case "init-host-relay":
                return cmd_init_host_relay(config)
            case "init-container":
                if args.json:
                    rc = _run_for_json(lambda: cmd_init_container(config, args.vmid))
                    _print_command_json(f"Initialized identity for {args.vmid}.", rc)
                    return rc
                rc = cmd_init_container(config, args.vmid)
                return rc
            case "init-shared":
                return cmd_init_shared(config)
            case "print-keepass-password":
                return cmd_print_keepass_password(config)
            case _:
                parser.error(f"unsupported command: {args.command}")
    except (ConfigError, PlanningError, ProxnixWorkstationError) as exc:
        if getattr(args, "json", False):
            print_json(json_error("secrets.failed", str(exc)))
        else:
            print(f"error: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
