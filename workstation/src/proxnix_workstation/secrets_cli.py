from __future__ import annotations

import argparse
from pathlib import Path

from .config import WorkstationConfig, load_workstation_config
from .errors import ConfigError, PlanningError, ProxnixWorkstationError
from .paths import SitePaths
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


def need_tools(config: WorkstationConfig) -> SitePaths:
    site_paths = SitePaths.from_config(config)
    ensure_commands(["sops"])
    return site_paths


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
    value = provider.get(container_scope(vmid), name)
    if value is not None:
        print(value)
        return 0

    matched_group: str | None = None
    for group in read_container_secret_groups(site_paths, vmid):
        if provider.get(group_scope(group), name) is not None:
            if matched_group is not None:
                raise ProxnixWorkstationError(
                    f"secret {name} is ambiguous for vmid={vmid}: found in groups {matched_group} and {group}"
                )
            matched_group = group

    if matched_group is not None:
        group_value = provider.get(group_scope(matched_group), name)
        if group_value is None:
            raise ProxnixWorkstationError(
                f"group secret disappeared while resolving vmid={vmid} name={name}"
            )
        print(group_value)
        return 0

    shared_value = provider.get(shared_scope(), name)
    if shared_value is None:
        raise ProxnixWorkstationError(f"secret not found for vmid={vmid}: {name}")
    print(shared_value)
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
    label, pubkey = create_identity_store(config, site_paths, site_paths.host_relay_identity_store, "host relay")
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
    label, pubkey = create_identity_store(
        config, site_paths, site_paths.container_identity_store(vmid), f"container {vmid}"
    )
    print(f"Initialized {label} SSH-backed age keypair")
    print(f"Public key: {pubkey}")
    return 0


def build_parser(*, prog: str = "proxnix-secrets") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--config", type=Path, help="Path to the proxnix workstation config file")
    sub = parser.add_subparsers(dest="command", required=True)

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
    sets = sub.add_parser("set-shared")
    sets.add_argument("name")
    setg = sub.add_parser("set-group")
    setg.add_argument("group")
    setg.add_argument("name")

    rmp = sub.add_parser("rm")
    rmp.add_argument("vmid")
    rmp.add_argument("name")
    rms = sub.add_parser("rm-shared")
    rms.add_argument("name")
    rmg = sub.add_parser("rm-group")
    rmg.add_argument("group")
    rmg.add_argument("name")

    rot = sub.add_parser("rotate")
    rot.add_argument("vmid")
    sub.add_parser("rotate-shared")
    rotg = sub.add_parser("rotate-group")
    rotg.add_argument("group")

    sub.add_parser("init-host-relay")
    initc = sub.add_parser("init-container")
    initc.add_argument("vmid")
    sub.add_parser("init-shared")
    return parser


def main(argv: list[str] | None = None, *, prog: str = "proxnix-secrets") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    config = load_workstation_config(args.config)

    try:
        match args.command:
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
                return cmd_set(config, args.vmid, args.name)
            case "set-shared":
                return cmd_set_shared(config, args.name)
            case "set-group":
                return cmd_set_group(config, args.group, args.name)
            case "rm":
                return cmd_rm(config, args.vmid, args.name)
            case "rm-shared":
                return cmd_rm_shared(config, args.name)
            case "rm-group":
                return cmd_rm_group(config, args.group, args.name)
            case "rotate":
                return cmd_rotate(config, args.vmid)
            case "rotate-shared":
                return cmd_rotate_shared(config)
            case "rotate-group":
                return cmd_rotate_group(config, args.group)
            case "init-host-relay":
                return cmd_init_host_relay(config)
            case "init-container":
                return cmd_init_container(config, args.vmid)
            case "init-shared":
                return cmd_init_shared(config)
            case _:
                parser.error(f"unsupported command: {args.command}")
    except (ConfigError, PlanningError, ProxnixWorkstationError) as exc:
        print(f"error: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
