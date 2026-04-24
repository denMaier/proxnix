from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import WorkstationConfig, load_workstation_config
from .errors import ConfigError, PlanningError, ProxnixWorkstationError
from .paths import SitePaths
from .provider_keys import (
    container_private_key_text,
    container_public_key,
    have_container_private_key,
    have_host_relay_private_key,
    host_relay_private_key_text,
    host_relay_public_key,
    master_private_key_text,
    write_private_key_file,
)
from .runtime import ensure_commands, run_command
from .secret_provider import (
    SecretProvider,
    container_scope,
    group_scope,
    load_secret_provider,
    shared_scope,
)
from .site import collect_site_vmids, read_container_secret_groups
from .sops_ops import (
    ensure_private_permissions,
    encrypt_identity_text_to_file,
    master_recipient,
    sops_encrypt_json_to_file,
)
from .ssh_ops import SSHSession


@dataclass
class PublishOptions:
    dry_run: bool = False
    report_changes: bool = False
    config_only: bool = False
    target_vmid: str | None = None


def need_publish_tools(config: WorkstationConfig, *, config_only: bool) -> SitePaths:
    site_paths = SitePaths.from_config(config)
    required_commands = ["ssh", "rsync"]
    if not config_only:
        required_commands.insert(0, "sops")
    ensure_commands(required_commands)
    if not config_only:
        provider = load_secret_provider(config, site_paths)
        master_private_key_text(config, provider)
    if config.ssh_identity is not None and not config.ssh_identity.is_file():
        raise ConfigError(f"publish SSH identity not found: {config.ssh_identity}")
    return site_paths


def validate_target_container_config(vmid: str) -> None:
    if not vmid.isdigit():
        raise ProxnixWorkstationError(f"container VMID must be numeric: {vmid}")


def container_has_group_store(site_paths: SitePaths, provider: SecretProvider, vmid: str) -> bool:
    for group in read_container_secret_groups(site_paths, vmid):
        if provider.has_any(group_scope(group)):
            return True
    return False


def validate_target_vmid_repo(
    config: WorkstationConfig,
    site_paths: SitePaths,
    provider: SecretProvider,
    vmid: str,
    *,
    config_only: bool,
) -> None:
    validate_target_container_config(vmid)
    if not (
        site_paths.container_dir(vmid).is_dir()
        or site_paths.container_identity_store(vmid).is_file()
        or provider.has_any(container_scope(vmid))
    ):
        raise ProxnixWorkstationError(f"no local config or secret state found for container {vmid}")
    if config_only:
        return

    if provider.has_any(container_scope(vmid)) and not have_container_private_key(config, provider, site_paths, vmid):
        raise ProxnixWorkstationError(
            f"container {vmid} has source secrets but no container identity for compiled delivery"
        )

    if (
        provider.has_any(container_scope(vmid))
        or have_container_private_key(config, provider, site_paths, vmid)
        or container_has_group_store(site_paths, provider, vmid)
    ) and not have_host_relay_private_key(config, provider, site_paths):
        raise ProxnixWorkstationError(
            "host relay identity is missing — run: proxnix-secrets init-host-relay"
        )


def container_has_any_store(site_paths: SitePaths, provider: SecretProvider, vmid: str) -> bool:
    if provider.has_any(shared_scope()) or provider.has_any(container_scope(vmid)):
        return True
    return container_has_group_store(site_paths, provider, vmid)


def validate_site_repo(config: WorkstationConfig, site_paths: SitePaths, provider: SecretProvider) -> None:
    for vmid in collect_site_vmids(site_paths):
        if container_has_any_store(site_paths, provider, vmid):
            if not have_container_private_key(config, provider, site_paths, vmid):
                raise ProxnixWorkstationError(
                    f"container {vmid} has source secrets but no container identity for compiled delivery"
                )
            if not have_host_relay_private_key(config, provider, site_paths):
                raise ProxnixWorkstationError(
                    "host relay identity is missing — run: proxnix-secrets init-host-relay"
                )


def build_compiled_secret_store(
    config: WorkstationConfig,
    site_paths: SitePaths,
    provider: SecretProvider,
    vmid: str,
    out_dir: Path,
) -> None:
    shared_data = provider.export_scope(shared_scope())
    group_data_by_name = {
        group: provider.export_scope(group_scope(group))
        for group in read_container_secret_groups(site_paths, vmid)
    }
    container_data = provider.export_scope(container_scope(vmid))
    have_any_store = bool(shared_data or container_data or any(group_data_by_name.values()))
    if not have_any_store:
        return

    merged: dict[str, str] = {}
    for key, value in shared_data.items():
        merged[key] = value

    seen_group_sources: dict[str, str] = {}
    for source_name, group_data in group_data_by_name.items():
        for key, value in group_data.items():
            if key in seen_group_sources:
                raise ProxnixWorkstationError(
                    f"grouped secret {key} is ambiguous: {seen_group_sources[key]} and {source_name}"
                )
            seen_group_sources[key] = source_name
            merged[key] = value

    for key, value in container_data.items():
        merged[key] = value

    if not have_container_private_key(config, provider, site_paths, vmid):
        raise ProxnixWorkstationError(
            f"container {vmid} compiled SOPS payload needs a container identity"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    master_key_text = master_private_key_text(config, provider)
    with tempfile.TemporaryDirectory(prefix="proxnix-publish-merged.") as temp_dir:
        plain_json = Path(temp_dir) / "effective.json"
        plain_json.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        recipients = ",".join(
            [
                container_public_key(config, provider, site_paths, vmid),
                master_recipient(config, master_private_key_text=master_key_text),
            ]
        )
        sops_encrypt_json_to_file(
            config,
            plain_json,
            recipients,
            out_dir / "effective.sops.yaml",
            master_private_key_text=master_key_text,
        )


def _copy_tree_if_present(source: Path, destination: Path) -> None:
    import shutil

    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def _copy_file_if_present(source: Path, destination: Path) -> None:
    import shutil

    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def build_publish_tree(config: WorkstationConfig, site_paths: SitePaths, options: PublishOptions, root: Path) -> None:
    provider = load_secret_provider(config, site_paths)
    private_root = root / "private"
    (root / "containers").mkdir(parents=True, exist_ok=True)
    (private_root / "containers").mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)
    (root / "containers").chmod(0o755)
    private_root.chmod(0o700)
    (private_root / "containers").chmod(0o700)

    if options.target_vmid is None and site_paths.site_nix.is_file():
        _copy_file_if_present(site_paths.site_nix, root / "site.nix")

    if options.target_vmid is not None:
        _copy_tree_if_present(site_paths.containers_dir / "_template", root / "containers" / "_template")
        _copy_tree_if_present(site_paths.container_dir(options.target_vmid), root / "containers" / options.target_vmid)
    else:
        _copy_tree_if_present(site_paths.containers_dir, root / "containers")

    if options.config_only:
        return

    if options.target_vmid is not None:
        build_compiled_secret_store(
            config,
            site_paths,
            provider,
            options.target_vmid,
            private_root / "containers" / options.target_vmid,
        )
    else:
        for vmid in collect_site_vmids(site_paths):
            build_compiled_secret_store(
                config,
                site_paths,
                provider,
                vmid,
                private_root / "containers" / vmid,
            )

    ensure_private_permissions(private_root)
def stage_identity_for_relay(
    config: WorkstationConfig,
    identity_private_text: str,
    recipients: str,
    destination: Path,
    cache_file: Path,
    *,
    master_private_key_text: str,
) -> None:
    source_hash = hashlib.sha256(identity_private_text.encode("utf-8")).hexdigest()
    sorted_recipients = ":".join(sorted(filter(None, recipients.split(",")))) + ":"
    inputs_hash = f"{source_hash}  {sorted_recipients}"
    inputs_file = cache_file.with_name(cache_file.name + ".inputs")

    if cache_file.is_file() and inputs_file.is_file() and inputs_file.read_text(encoding="utf-8") == inputs_hash:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(cache_file.read_text(encoding="utf-8"), encoding="utf-8")
        destination.chmod(0o600)
        return

    encrypt_identity_text_to_file(
        config,
        identity_private_text,
        recipients,
        destination,
        master_private_key_text=master_private_key_text,
    )
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(destination.read_text(encoding="utf-8"), encoding="utf-8")
    cache_file.chmod(0o600)
    inputs_file.write_text(inputs_hash, encoding="utf-8")
    inputs_file.chmod(0o600)


def stage_relay_identities_into_tree(
    config: WorkstationConfig,
    site_paths: SitePaths,
    options: PublishOptions,
    tree: Path,
) -> None:
    provider = load_secret_provider(config, site_paths)
    private_root = tree / "private"
    master_key_text = master_private_key_text(config, provider)
    relay_private_text = host_relay_private_key_text(config, provider, site_paths)
    if relay_private_text is None:
        return
    relay_recipient = host_relay_public_key(config, provider, site_paths)
    identity_recipients = ",".join(
        [relay_recipient, master_recipient(config, master_private_key_text=master_key_text)]
    )

    relay_private = private_root / "host_relay_identity"
    write_private_key_file(relay_private_text, relay_private)

    if options.target_vmid is not None:
        identity_private_text = container_private_key_text(config, provider, site_paths, options.target_vmid)
        if identity_private_text is not None:
            dest = private_root / "containers" / options.target_vmid / "age_identity.sops.yaml"
            stage_identity_for_relay(
                config,
                identity_private_text,
                identity_recipients,
                dest,
                site_paths.relay_cache_container_identity(options.target_vmid),
                master_private_key_text=master_key_text,
            )
        return

    for vmid in collect_site_vmids(site_paths):
        identity_private_text = container_private_key_text(config, provider, site_paths, vmid)
        if identity_private_text is None:
            continue
        dest = private_root / "containers" / vmid / "age_identity.sops.yaml"
        stage_identity_for_relay(
            config,
            identity_private_text,
            identity_recipients,
            dest,
            site_paths.relay_cache_container_identity(vmid),
            master_private_key_text=master_key_text,
        )


def should_report_change(config: WorkstationConfig, path: PurePosixPath) -> bool:
    if path == config.remote_dir / "site.nix":
        return True
    try:
        path.relative_to(config.remote_dir / "containers")
        return True
    except ValueError:
        return False


def append_host_change(report: list[tuple[str, PurePosixPath]], kind: str, path: PurePosixPath) -> None:
    report.append((kind, path))


def note_remote_file_change(
    session: SSHSession,
    config: WorkstationConfig,
    path: PurePosixPath,
    report: list[tuple[str, PurePosixPath]],
) -> None:
    completed = session.run(f"test -e {shlex.quote(str(path))}", check=False, capture_output=True)
    if completed.returncode == 0 and should_report_change(config, path):
        append_host_change(report, "delete", path)


def print_host_summary(host: str, report: list[tuple[str, PurePosixPath]]) -> None:
    if not report:
        print(f"  No remote changes for {host}")
        return
    creates = sum(1 for kind, _ in report if kind == "create")
    updates = sum(1 for kind, _ in report if kind == "update")
    deletes = sum(1 for kind, _ in report if kind == "delete")
    print(f"  Changes for {host}: {len(report)} total ({creates} created, {updates} updated, {deletes} deleted)")
    for kind, path in report:
        print(f"    {kind} {path}")


def do_rsync(
    session: SSHSession,
    config: WorkstationConfig,
    source: Path,
    destination: PurePosixPath,
    *,
    directory_contents: bool = False,
    delete: bool,
    dry_run: bool,
    report: list[tuple[str, PurePosixPath]] | None,
) -> None:
    args = [
        "rsync",
        "-a",
        "--no-owner",
        "--no-group",
        "-e",
        session.rsync_ssh_command(),
    ]
    source_arg = str(source)
    if directory_contents:
        source_arg = source_arg.rstrip("/") + "/"

    if delete:
        args.append("--delete")
    if dry_run:
        args.append("--dry-run")
    if report is not None:
        args.extend(["--itemize-changes", "--out-format=%i %n%L"])

    args.extend([source_arg, f"{session.host}:{destination}"])
    completed = run_command(args, check=True, capture_output=report is not None)

    if report is None:
        return

    for raw_line in completed.stdout.splitlines():
        if not raw_line:
            continue
        if raw_line.startswith("*deleting "):
            rel = raw_line[len("*deleting ") :]
            remote_path = PurePosixPath(str(destination).rstrip("/") + "/" + rel)
            if should_report_change(config, remote_path):
                append_host_change(report, "delete", remote_path)
            continue
        item, _, relpath = raw_line.partition(" ")
        if directory_contents:
            if relpath.endswith("/"):
                continue
            remote_path = PurePosixPath(str(destination).rstrip("/") + "/" + relpath)
        else:
            remote_path = destination

        if "+++++++++" in item:
            kind = "create"
        elif item.startswith("."):
            continue
        else:
            kind = "update"
        if should_report_change(config, remote_path):
            append_host_change(report, kind, remote_path)


def sync_path(
    session: SSHSession,
    config: WorkstationConfig,
    source_dir: Path,
    destination: PurePosixPath,
    *,
    dry_run: bool,
    report: list[tuple[str, PurePosixPath]] | None,
) -> None:
    do_rsync(
        session,
        config,
        source_dir,
        destination,
        directory_contents=True,
        delete=True,
        dry_run=dry_run,
        report=report,
    )


def sync_file(
    session: SSHSession,
    config: WorkstationConfig,
    source_file: Path,
    destination: PurePosixPath,
    *,
    dry_run: bool,
    report: list[tuple[str, PurePosixPath]] | None,
) -> None:
    do_rsync(session, config, source_file, destination, delete=False, dry_run=dry_run, report=report)


def remove_remote_file(
    session: SSHSession,
    config: WorkstationConfig,
    path: PurePosixPath,
    *,
    dry_run: bool,
    report: list[tuple[str, PurePosixPath]] | None,
) -> None:
    if report is not None:
        note_remote_file_change(session, config, path, report)
    if dry_run:
        return
    session.run(f"rm -f {shlex.quote(str(path))}", capture_output=True)


def ensure_remote_dirs(session: SSHSession, config: WorkstationConfig, *, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] ensure relay dirs on {session.host}")
        return
    command = (
        f"mkdir -p {shlex.quote(str(config.remote_dir / 'containers'))} "
        f"{shlex.quote(str(config.remote_priv_dir / 'containers'))} "
        f"{shlex.quote(str(config.remote_host_relay_identity.parent))} && "
        f"chmod 755 {shlex.quote(str(config.remote_dir))} {shlex.quote(str(config.remote_dir / 'containers'))} && "
        f"chmod 700 {shlex.quote(str(config.remote_priv_dir))} {shlex.quote(str(config.remote_priv_dir / 'containers'))} "
        f"{shlex.quote(str(config.remote_host_relay_identity.parent))}"
    )
    session.run(command, capture_output=True)


def publish_host(
    session: SSHSession,
    config: WorkstationConfig,
    options: PublishOptions,
    tree: Path,
) -> None:
    report: list[tuple[str, PurePosixPath]] | None = [] if options.report_changes else None
    print(f"Publishing relay cache to {session.host}")
    ensure_remote_dirs(session, config, dry_run=options.dry_run)

    site_nix = tree / "site.nix"
    if site_nix.is_file():
        sync_file(session, config, site_nix, config.remote_dir / "site.nix", dry_run=options.dry_run, report=report)
    else:
        remove_remote_file(session, config, config.remote_dir / "site.nix", dry_run=options.dry_run, report=report)

    sync_path(session, config, tree / "containers", config.remote_dir / "containers", dry_run=options.dry_run, report=report)

    if options.config_only:
        if report is not None:
            print_host_summary(session.host, report)
        return

    sync_path(
        session,
        config,
        tree / "private" / "containers",
        config.remote_priv_dir / "containers",
        dry_run=options.dry_run,
        report=report,
    )

    relay_private = tree / "private" / "host_relay_identity"
    if relay_private.is_file():
        sync_file(
            session,
            config,
            relay_private,
            config.remote_host_relay_identity,
            dry_run=options.dry_run,
            report=report,
        )
    else:
        remove_remote_file(
            session,
            config,
            config.remote_host_relay_identity,
            dry_run=options.dry_run,
            report=report,
        )

    if report is not None:
        print_host_summary(session.host, report)


def publish_vmid_host(
    session: SSHSession,
    config: WorkstationConfig,
    options: PublishOptions,
    tree: Path,
) -> None:
    assert options.target_vmid is not None
    vmid = options.target_vmid
    report: list[tuple[str, PurePosixPath]] | None = [] if options.report_changes else None
    if options.config_only:
        print(f"Publishing config for container {vmid} to {session.host}")
    else:
        print(f"Publishing container {vmid} to {session.host}")
    ensure_remote_dirs(session, config, dry_run=options.dry_run)

    template_source = tree / "containers" / "_template"
    if template_source.is_dir():
        sync_path(
            session,
            config,
            template_source,
            config.remote_dir / "containers" / "_template",
            dry_run=options.dry_run,
            report=report,
        )

    source_dir = tree / "containers" / vmid
    remote_dir = config.remote_dir / "containers" / vmid
    if source_dir.is_dir():
        sync_path(session, config, source_dir, remote_dir, dry_run=options.dry_run, report=report)
    else:
        if report is not None:
            note_remote_file_change(session, config, remote_dir, report)
        if not options.dry_run:
            session.run(f"rm -rf {shlex.quote(str(remote_dir))}", capture_output=True)

    if not options.config_only:
        source_private = tree / "private" / "containers" / vmid
        remote_private = config.remote_priv_dir / "containers" / vmid
        if source_private.is_dir():
            sync_path(session, config, source_private, remote_private, dry_run=options.dry_run, report=report)
        elif not options.dry_run:
            session.run(f"rm -rf {shlex.quote(str(remote_private))}", capture_output=True)

        relay_private = tree / "private" / "host_relay_identity"
        if relay_private.is_file():
            sync_file(
                session,
                config,
                relay_private,
                config.remote_host_relay_identity,
                dry_run=options.dry_run,
                report=report,
            )

    if report is not None:
        print_host_summary(session.host, report)


def publish_selected_host(session: SSHSession, config: WorkstationConfig, options: PublishOptions, tree: Path) -> None:
    if options.target_vmid is not None:
        publish_vmid_host(session, config, options, tree)
    else:
        publish_host(session, config, options, tree)


def build_parser(*, prog: str = "proxnix-publish") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--config", type=Path, help="Path to the proxnix workstation config file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-changes", action="store_true")
    parser.add_argument("--config-only", action="store_true")
    parser.add_argument("--vmid")
    parser.add_argument("--container-config")
    parser.add_argument("hosts", nargs="*")
    return parser


def main(argv: list[str] | None = None, *, prog: str = "proxnix-publish") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    config = load_workstation_config(args.config)
    options = PublishOptions(
        dry_run=args.dry_run,
        report_changes=args.report_changes,
        config_only=args.config_only,
        target_vmid=args.vmid,
    )

    if args.container_config is not None:
        options.target_vmid = args.container_config
        options.config_only = True

    try:
        site_paths = need_publish_tools(config, config_only=options.config_only)
        provider = load_secret_provider(config, site_paths)
        if options.target_vmid is not None:
            validate_target_vmid_repo(
                config,
                site_paths,
                provider,
                options.target_vmid,
                config_only=options.config_only,
            )
        elif not options.config_only:
            validate_site_repo(config, site_paths, provider)

        hosts = list(args.hosts) if args.hosts else list(config.hosts)
        if not hosts:
            raise ProxnixWorkstationError("no publish hosts configured")

        with tempfile.TemporaryDirectory(prefix="proxnix-publish.") as temp_dir:
            temp_root = Path(temp_dir)
            relay_tree = temp_root / "relay"
            build_publish_tree(config, site_paths, options, relay_tree)

            for host in hosts:
                with SSHSession(config, host, temp_root=temp_root) as session:
                    if not options.config_only and have_host_relay_private_key(config, provider, site_paths):
                        stage_relay_identities_into_tree(config, site_paths, options, relay_tree)
                    publish_selected_host(session, config, options, relay_tree)

        print("Publish complete")
        return 0
    except (ConfigError, PlanningError, ProxnixWorkstationError) as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
