from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import WorkstationConfig, load_workstation_config
from .errors import ConfigError, PlanningError, ProxnixWorkstationError
from .paths import SitePaths
from .runtime import ensure_commands
from .site import read_container_secret_groups, top_level_keys, valid_secret_group_name
from .sops_ops import (
    ensure_flat_secret_map,
    ensure_private_permissions,
    generate_identity_keypair,
    identity_public_key_from_store,
    master_recipient,
    read_secret_value,
    reencrypt_identity_store_to_file,
    sops_decrypt_json,
    sops_encrypt_json_text,
    sops_encrypt_yaml_text,
    write_identity_payload,
)


def need_tools(config: WorkstationConfig) -> SitePaths:
    site_paths = SitePaths.from_config(config)
    ensure_commands(["sops"])
    return site_paths


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


def ensure_store(config: WorkstationConfig, site_paths: SitePaths, path: Path, recipients: str) -> None:
    if path.exists():
        return
    ensure_secret_parent_dir(site_paths, path)
    encrypted = sops_encrypt_json_text(config, "{}\n", recipients)
    write_store_atomic(path, encrypted)


def read_store_map(config: WorkstationConfig, path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    return ensure_flat_secret_map(sops_decrypt_json(config, path), source=str(path))


def write_store_map(config: WorkstationConfig, path: Path, recipients: str, data: dict[str, str]) -> None:
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
    ensure_store(config, site_paths, path, recipients)
    data = read_store_map(config, path) or {}
    data[name] = value
    write_store_map(config, path, recipients, data)


def sops_unset_local(config: WorkstationConfig, path: Path, recipients: str, name: str) -> None:
    if not path.exists():
        return
    data = read_store_map(config, path) or {}
    if name not in data:
        return
    del data[name]
    write_store_map(config, path, recipients, data)


def sops_get_local(config: WorkstationConfig, path: Path, name: str) -> str | None:
    data = read_store_map(config, path)
    if data is None:
        return None
    return data.get(name)


def reencrypt_local(config: WorkstationConfig, path: Path, recipients: str) -> None:
    if not path.exists():
        raise ProxnixWorkstationError(f"store not found: {path}")
    data = read_store_map(config, path) or {}
    write_store_map(config, path, recipients, data)


def cmd_ls(config: WorkstationConfig, vmid: str | None) -> int:
    site_paths = need_tools(config)
    if vmid is None:
        if not site_paths.private_dir.is_dir():
            return 0
        rows: list[tuple[str, str]] = []
        for store in sorted(site_paths.private_dir.rglob("secrets.sops.yaml")):
            if store == site_paths.shared_store:
                prefix = "shared"
            elif "/groups/" in str(store):
                group = store.parent.parent.name
                prefix = f"group:{group}"
            elif "/containers/" in str(store):
                prefix = store.parent.name
            else:
                continue
            for key in top_level_keys(store):
                rows.append((prefix, key))
        for prefix, key in sorted(rows):
            print(f"{prefix}\t{key}")
        return 0

    seen: dict[str, str] = {}
    if site_paths.shared_store.exists():
        for key in top_level_keys(site_paths.shared_store):
            seen.setdefault(key, "shared")
    for group in read_container_secret_groups(site_paths, vmid):
        store = site_paths.group_store(group)
        if not store.exists():
            continue
        for key in top_level_keys(store):
            if seen.get(key) != "container":
                seen[key] = f"group:{group}"
    container_store = site_paths.container_store(vmid)
    if container_store.exists():
        for key in top_level_keys(container_store):
            seen[key] = "container"
    for key in sorted(seen):
        print(f"{key}\t{seen[key]}")
    return 0


def cmd_ls_shared(config: WorkstationConfig) -> int:
    site_paths = need_tools(config)
    for key in top_level_keys(site_paths.shared_store):
        print(key)
    return 0


def cmd_ls_group(config: WorkstationConfig, group: str) -> int:
    site_paths = need_tools(config)
    if not valid_secret_group_name(group):
        raise ProxnixWorkstationError(f"invalid group name: {group}")
    for key in top_level_keys(site_paths.group_store(group)):
        print(key)
    return 0


def cmd_get(config: WorkstationConfig, vmid: str, name: str) -> int:
    site_paths = need_tools(config)
    value = sops_get_local(config, site_paths.container_store(vmid), name)
    if value is not None:
        print(value)
        return 0

    matched_group: str | None = None
    for group in read_container_secret_groups(site_paths, vmid):
        group_path = site_paths.group_store(group)
        if not group_path.exists():
            continue
        if sops_get_local(config, group_path, name) is not None:
            if matched_group is not None:
                raise ProxnixWorkstationError(
                    f"secret {name} is ambiguous for vmid={vmid}: found in groups {matched_group} and {group}"
                )
            matched_group = group

    if matched_group is not None:
        group_value = sops_get_local(config, site_paths.group_store(matched_group), name)
        if group_value is None:
            raise ProxnixWorkstationError(
                f"group secret disappeared while resolving vmid={vmid} name={name}"
            )
        print(group_value)
        return 0

    shared_value = sops_get_local(config, site_paths.shared_store, name)
    if shared_value is None:
        raise ProxnixWorkstationError(f"secret not found for vmid={vmid}: {name}")
    print(shared_value)
    return 0


def cmd_get_shared(config: WorkstationConfig, name: str) -> int:
    site_paths = need_tools(config)
    value = sops_get_local(config, site_paths.shared_store, name)
    if value is None:
        raise ProxnixWorkstationError(f"shared secret not found: {name}")
    print(value)
    return 0


def cmd_get_group(config: WorkstationConfig, group: str, name: str) -> int:
    site_paths = need_tools(config)
    if not valid_secret_group_name(group):
        raise ProxnixWorkstationError(f"invalid group name: {group}")
    value = sops_get_local(config, site_paths.group_store(group), name)
    if value is None:
        raise ProxnixWorkstationError(f"group secret not found: group={group} name={name}")
    print(value)
    return 0


def cmd_set(config: WorkstationConfig, vmid: str, name: str) -> int:
    site_paths = need_tools(config)
    value = read_secret_value()
    recipients = container_recipients(config, site_paths, vmid)
    sops_set_local(config, site_paths, site_paths.container_store(vmid), recipients, name, value)
    print(f"Set: vmid={vmid} name={name}")
    return 0


def cmd_set_shared(config: WorkstationConfig, name: str) -> int:
    site_paths = need_tools(config)
    value = read_secret_value()
    recipients = shared_recipients(config, site_paths)
    sops_set_local(config, site_paths, site_paths.shared_store, recipients, name, value)
    print(f"Set shared: name={name}")
    return 0


def cmd_set_group(config: WorkstationConfig, group: str, name: str) -> int:
    site_paths = need_tools(config)
    if not valid_secret_group_name(group):
        raise ProxnixWorkstationError(f"invalid group name: {group}")
    value = read_secret_value()
    recipients = group_recipients(config, site_paths)
    sops_set_local(config, site_paths, site_paths.group_store(group), recipients, name, value)
    print(f"Set group: group={group} name={name}")
    return 0


def cmd_rm(config: WorkstationConfig, vmid: str, name: str) -> int:
    site_paths = need_tools(config)
    recipients = container_recipients(config, site_paths, vmid)
    sops_unset_local(config, site_paths.container_store(vmid), recipients, name)
    print(f"Removed: vmid={vmid} name={name}")
    return 0


def cmd_rm_shared(config: WorkstationConfig, name: str) -> int:
    site_paths = need_tools(config)
    recipients = shared_recipients(config, site_paths)
    sops_unset_local(config, site_paths.shared_store, recipients, name)
    print(f"Removed shared: name={name}")
    return 0


def cmd_rm_group(config: WorkstationConfig, group: str, name: str) -> int:
    site_paths = need_tools(config)
    if not valid_secret_group_name(group):
        raise ProxnixWorkstationError(f"invalid group name: {group}")
    recipients = group_recipients(config, site_paths)
    sops_unset_local(config, site_paths.group_store(group), recipients, name)
    print(f"Removed group secret: group={group} name={name}")
    return 0


def cmd_rotate(config: WorkstationConfig, vmid: str) -> int:
    site_paths = need_tools(config)
    recipients = container_recipients(config, site_paths, vmid)
    reencrypt_local(config, site_paths.container_store(vmid), recipients)
    print(f"Rotated store: vmid={vmid}")
    return 0


def cmd_rotate_shared(config: WorkstationConfig) -> int:
    site_paths = need_tools(config)
    recipients = shared_recipients(config, site_paths)
    reencrypt_local(config, site_paths.shared_store, recipients)
    print("Rotated shared store")
    return 0


def cmd_rotate_group(config: WorkstationConfig, group: str) -> int:
    site_paths = need_tools(config)
    if not valid_secret_group_name(group):
        raise ProxnixWorkstationError(f"invalid group name: {group}")
    recipients = group_recipients(config, site_paths)
    reencrypt_local(config, site_paths.group_store(group), recipients)
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
