from __future__ import annotations

import argparse
import contextlib
import io
import hashlib
import json
import os
import shlex
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import WorkstationConfig, load_workstation_config
from .errors import ConfigError, PlanningError, ProxnixWorkstationError
from .json_api import error as json_error
from .json_api import ok as json_ok
from .json_api import print_json
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
    write_secret_bundle_map,
)
from .ssh_ops import SSHSession


@dataclass
class PublishOptions:
    dry_run: bool = False
    report_changes: bool = False
    config_only: bool = False
    target_vmid: str | None = None
    reconcile: bool = False


@dataclass(frozen=True)
class PublishSource:
    site_paths: SitePaths
    commit: str | None
    branch: str | None
    dirty: bool = False
    using_head: bool = False


def _git(site_dir: Path, *args: str, check: bool = True):
    return run_command(["git", "-C", str(site_dir), *args], check=check, capture_output=True)


def _git_output(site_dir: Path, *args: str) -> str:
    return _git(site_dir, *args).stdout.strip()


def is_git_worktree(site_dir: Path) -> bool:
    result = _git(site_dir, "rev-parse", "--is-inside-work-tree", check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _uses_embedded_site_secrets(config: WorkstationConfig) -> bool:
    return config.secret_provider in {"embedded-age", "embedded-sops"}


def _head_paths_for_publish(config: WorkstationConfig, options: PublishOptions) -> list[str]:
    if options.target_vmid is None:
        paths = ["site.nix", "flake.lock", "containers"]
        if _uses_embedded_site_secrets(config):
            paths.append("private")
        return paths

    paths = [
        "flake.lock",
        "containers/_template",
        f"containers/{options.target_vmid}",
    ]
    if _uses_embedded_site_secrets(config):
        paths.extend(
            [
                "private/host_relay_identity.age",
                f"private/containers/{options.target_vmid}",
                "private/shared",
                "private/groups",
            ]
        )
    return paths


def _existing_head_paths(site_dir: Path, paths: list[str]) -> list[str]:
    existing: list[str] = []
    for path in paths:
        result = _git(site_dir, "cat-file", "-e", f"HEAD:{path}", check=False)
        if result.returncode == 0:
            existing.append(path)
    return existing


def materialize_head_site(
    config: WorkstationConfig,
    site_paths: SitePaths,
    destination: Path,
    options: PublishOptions | None = None,
) -> PublishSource:
    source_dir = site_paths.site_dir
    if not is_git_worktree(source_dir):
        print("warning: site directory is not a git worktree; publishing the live worktree")
        return PublishSource(site_paths=site_paths, commit=None, branch=None)

    commit = _git_output(source_dir, "rev-parse", "HEAD")
    branch = _git_output(source_dir, "branch", "--show-current") or None
    status = _git_output(source_dir, "status", "--porcelain=v1", "-uall")
    dirty = bool(status)
    if dirty:
        print(
            "warning: site repo has uncommitted changes; publish uses HEAD "
            "and ignores staged, unstaged, and untracked files"
        )

    top_level = Path(_git_output(source_dir, "rev-parse", "--show-toplevel"))
    prefix = _git_output(source_dir, "rev-parse", "--show-prefix").rstrip("/")
    treeish = f"HEAD:{prefix}" if prefix else "HEAD"
    archive_paths = _existing_head_paths(source_dir, _head_paths_for_publish(config, options or PublishOptions()))
    archive_path = destination.with_suffix(".tar")
    destination.mkdir(parents=True, exist_ok=True)
    if archive_paths:
        _git(source_dir, "archive", "--format=tar", f"--output={archive_path}", treeish, "--", *archive_paths)
        with tarfile.open(archive_path) as archive:
            archive.extractall(destination, filter="data")
        archive_path.unlink()

    label = f"{commit[:12]}"
    if branch is not None:
        label = f"{label} on {branch}"
    if top_level != source_dir:
        try:
            label = f"{label} ({source_dir.relative_to(top_level)})"
        except ValueError:
            pass
    print(f"Publishing site repo HEAD {label}")
    return PublishSource(site_paths=SitePaths(destination), commit=commit, branch=branch, dirty=dirty, using_head=True)


def write_publish_revision(source: PublishSource, destination: Path) -> None:
    payload = {
        "branch": source.branch,
        "commit": source.commit,
        "dirty_worktree_ignored": source.dirty,
        "source": "git-head" if source.using_head else "worktree",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    destination.chmod(0o644)


def need_publish_tools(config: WorkstationConfig, *, config_only: bool) -> SitePaths:
    site_paths = SitePaths.from_config(config)
    required_commands = ["git", "ssh", "rsync"]
    if not config_only:
        required_commands.insert(0, "age")
        required_commands.insert(1, "ssh-keygen")
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
            f"container {vmid} compiled secret bundle needs a container identity"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    master_key_text = master_private_key_text(config, provider)
    recipients = ",".join(
        [
            container_public_key(config, provider, site_paths, vmid),
            master_recipient(config, master_private_key_text=master_key_text),
        ]
    )
    write_secret_bundle_map(out_dir / "effective.secrets.json", recipients, merged)


def _copy_file_if_present(source: Path, destination: Path) -> None:
    import shutil

    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _render_modules_nix(*, dropin_names: list[str]) -> str:
    lines = [
        "# Generated by proxnix workstation publish; completed by proxnix-host authority render.",
        "let",
        "  site = ../../site.nix;",
        "in",
        "[",
        "  ../../modules/proxnix-guest-base.nix",
    ]
    lines.append("] ++ (if builtins.pathExists site then [ site ] else []) ++ [")
    lines.append("  ./proxmox.nix")
    for name in dropin_names:
        lines.append(f"  ./dropins/{name}")
    lines.extend(["]", ""])
    return "\n".join(lines)


def _render_container_authority_inputs(site_paths: SitePaths, vmid: str, out_dir: Path) -> None:
    import shutil

    out_dir.mkdir(parents=True, exist_ok=True)
    dropins_out = out_dir / "dropins"
    runtime_bin_out = out_dir / "runtime-bin"
    template_out = out_dir / "_template"
    dropins_out.mkdir(parents=True, exist_ok=True)

    dropin_names: list[str] = []
    dropin_source = site_paths.container_dir(vmid) / "dropins"
    if dropin_source.is_dir():
        for source in sorted(dropin_source.iterdir(), key=lambda item: item.name):
            if source.is_file():
                suffix = source.suffix
                if suffix == ".nix":
                    shutil.copy2(source, dropins_out / source.name)
                    dropin_names.append(source.name)
                elif suffix in {".sh", ".py"}:
                    runtime_bin_out.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, runtime_bin_out / source.name)
                elif suffix == ".service":
                    raise ProxnixWorkstationError(
                        f"host-side dropins/*.service are no longer supported: {source}"
                    )
                elif suffix in {".container", ".volume", ".network", ".pod", ".image", ".build"}:
                    raise ProxnixWorkstationError(
                        f"raw Quadlet drop-ins are no longer supported: {source}"
                    )
            elif source.is_dir():
                shutil.copytree(source, dropins_out / source.name, dirs_exist_ok=True)

    selector_dir = site_paths.container_dir(vmid) / "templates"
    template_root = site_paths.containers_dir / "_template"
    if selector_dir.is_dir():
        for selector in sorted(selector_dir.iterdir(), key=lambda item: item.name):
            if not selector.is_file() or selector.suffix != ".template":
                continue
            template_name = selector.name.removesuffix(".template")
            source = template_root / template_name
            if not source.is_dir():
                raise ProxnixWorkstationError(f"selected template not found: {source}")
            shutil.copytree(source, template_out / template_name, dirs_exist_ok=True)

    (out_dir / "modules.nix").write_text(
        _render_modules_nix(dropin_names=dropin_names),
        encoding="utf-8",
    )


def build_publish_tree(config: WorkstationConfig, site_paths: SitePaths, options: PublishOptions, root: Path) -> None:
    provider = load_secret_provider(config, site_paths)
    private_root = root / "private"
    authority_root = root / "authority"
    authority_containers = authority_root / "containers"
    authority_containers.mkdir(parents=True, exist_ok=True)
    (private_root / "containers").mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)
    authority_root.chmod(0o755)
    authority_containers.chmod(0o755)
    private_root.chmod(0o700)
    (private_root / "containers").chmod(0o700)

    has_site = options.target_vmid is None and site_paths.site_nix.is_file()
    if has_site:
        _copy_file_if_present(site_paths.site_nix, authority_root / "site.nix")
    _copy_file_if_present(site_paths.flake_lock, authority_root / "flake.lock")

    if options.target_vmid is not None:
        vmids = [options.target_vmid]
    else:
        vmids = collect_site_vmids(site_paths)
    for vmid in vmids:
        _render_container_authority_inputs(
            site_paths,
            vmid,
            authority_containers / vmid,
        )

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
            dest = private_root / "containers" / options.target_vmid / "age_identity.age"
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
        dest = private_root / "containers" / vmid / "age_identity.age"
        stage_identity_for_relay(
            config,
            identity_private_text,
            identity_recipients,
            dest,
            site_paths.relay_cache_container_identity(vmid),
            master_private_key_text=master_key_text,
        )


def should_report_change(config: WorkstationConfig, path: PurePosixPath) -> bool:
    if path in {
        config.remote_dir / "authority" / "flake.lock",
        config.remote_dir / "authority" / "publish-revision.json",
    }:
        return True
    try:
        path.relative_to(config.remote_dir / "authority")
        return True
    except ValueError:
        pass
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


def host_report_data(host: str, report: list[tuple[str, PurePosixPath]]) -> dict[str, object]:
    return {
        "host": host,
        "changed": bool(report),
        "creates": sum(1 for kind, _ in report if kind == "create"),
        "updates": sum(1 for kind, _ in report if kind == "update"),
        "deletes": sum(1 for kind, _ in report if kind == "delete"),
        "changes": [
            {
                "action": kind,
                "path": str(path),
            }
            for kind, path in report
        ],
    }


def host_api_after_publish_command(options: PublishOptions) -> str:
    if options.dry_run:
        command = ["proxnix-host", "api", "plan"]
        if options.target_vmid is not None:
            command.extend(["--vmid", options.target_vmid])
        else:
            command.append("--all-ct")
        return shlex.join(command)

    command = ["proxnix-host", "api", "site-updated"]
    return shlex.join(command)


def reconcile_remote_command(options: PublishOptions) -> str:
    command = ["proxnix-reconcile"]
    if options.dry_run:
        command.append("--dry-run")
    if options.target_vmid is not None:
        command.extend(["--vmid", options.target_vmid])
    return shlex.join(command)


def run_remote_reconcile(session: SSHSession, options: PublishOptions) -> dict[str, object]:
    command = reconcile_remote_command(options)
    completed = session.run(command, check=False, capture_output=True)
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(completed.stderr.rstrip())
    return {
        "command": command,
        "exitCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def run_remote_host_api_after_publish(session: SSHSession, options: PublishOptions) -> dict[str, object]:
    command = host_api_after_publish_command(options)
    completed = session.run(command, check=False, capture_output=True)
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(completed.stderr.rstrip())
    return {
        "command": command,
        "exitCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


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
        f"{shlex.quote(str(config.remote_dir / 'authority'))} "
        f"{shlex.quote(str(config.remote_dir / 'authority' / 'containers'))} "
        f"{shlex.quote(str(config.remote_priv_dir / 'containers'))} "
        f"{shlex.quote(str(config.remote_host_relay_identity.parent))} && "
        f"chmod 755 {shlex.quote(str(config.remote_dir))} {shlex.quote(str(config.remote_dir / 'containers'))} "
        f"{shlex.quote(str(config.remote_dir / 'authority'))} "
        f"{shlex.quote(str(config.remote_dir / 'authority' / 'containers'))} && "
        f"chmod 700 {shlex.quote(str(config.remote_priv_dir))} {shlex.quote(str(config.remote_priv_dir / 'containers'))} "
        f"{shlex.quote(str(config.remote_host_relay_identity.parent))}"
    )
    session.run(command, capture_output=True)


def publish_host(
    session: SSHSession,
    config: WorkstationConfig,
    options: PublishOptions,
    tree: Path,
) -> list[tuple[str, PurePosixPath]]:
    report: list[tuple[str, PurePosixPath]] | None = [] if options.report_changes else None
    print(f"Publishing relay cache to {session.host}")
    ensure_remote_dirs(session, config, dry_run=options.dry_run)

    site_nix = tree / "authority" / "site.nix"
    if site_nix.is_file():
        sync_file(
            session,
            config,
            site_nix,
            config.remote_dir / "authority" / "site.nix",
            dry_run=options.dry_run,
            report=report,
        )
    else:
        remove_remote_file(
            session,
            config,
            config.remote_dir / "authority" / "site.nix",
            dry_run=options.dry_run,
            report=report,
        )

    revision = tree / "authority" / "publish-revision.json"
    sync_file(
        session,
        config,
        revision,
        config.remote_dir / "authority" / "publish-revision.json",
        dry_run=options.dry_run,
        report=report,
    )

    flake_lock = tree / "authority" / "flake.lock"
    if flake_lock.is_file():
        sync_file(
            session,
            config,
            flake_lock,
            config.remote_dir / "authority" / "flake.lock",
            dry_run=options.dry_run,
            report=report,
        )

    sync_path(
        session,
        config,
        tree / "authority" / "containers",
        config.remote_dir / "authority" / "containers",
        dry_run=options.dry_run,
        report=report,
    )

    if options.config_only:
        if report is not None:
            print_host_summary(session.host, report)
            return report
        return []

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
        return report
    return []


def publish_vmid_host(
    session: SSHSession,
    config: WorkstationConfig,
    options: PublishOptions,
    tree: Path,
) -> list[tuple[str, PurePosixPath]]:
    assert options.target_vmid is not None
    vmid = options.target_vmid
    report: list[tuple[str, PurePosixPath]] | None = [] if options.report_changes else None
    if options.config_only:
        print(f"Publishing config for container {vmid} to {session.host}")
    else:
        print(f"Publishing container {vmid} to {session.host}")
    ensure_remote_dirs(session, config, dry_run=options.dry_run)

    revision = tree / "authority" / "publish-revision.json"
    sync_file(
        session,
        config,
        revision,
        config.remote_dir / "authority" / "publish-revision.json",
        dry_run=options.dry_run,
        report=report,
    )

    flake_lock = tree / "authority" / "flake.lock"
    if flake_lock.is_file():
        sync_file(
            session,
            config,
            flake_lock,
            config.remote_dir / "authority" / "flake.lock",
            dry_run=options.dry_run,
            report=report,
        )

    source_dir = tree / "authority" / "containers" / vmid
    remote_dir = config.remote_dir / "authority" / "containers" / vmid
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
        return report
    return []


def publish_selected_host(
    session: SSHSession,
    config: WorkstationConfig,
    options: PublishOptions,
    tree: Path,
) -> list[tuple[str, PurePosixPath]]:
    if options.target_vmid is not None:
        return publish_vmid_host(session, config, options, tree)
    return publish_host(session, config, options, tree)


def build_parser(*, prog: str = "proxnix-publish") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--config", type=Path, help="Path to the proxnix workstation config file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-changes", action="store_true")
    parser.add_argument("--config-only", action="store_true")
    parser.set_defaults(reconcile=None)
    parser.add_argument(
        "--reconcile",
        dest="reconcile",
        action="store_true",
        help="Notify each host through proxnix-host api after publish; default for non-dry-run publishes",
    )
    parser.add_argument(
        "--no-reconcile",
        dest="reconcile",
        action="store_false",
        help="Publish files only and skip the host API notification",
    )
    parser.add_argument("--vmid")
    parser.add_argument("--host", action="append", dest="option_hosts", help="Remote host; may be repeated")
    parser.add_argument("--container-config")
    parser.add_argument("--json", action="store_true", help="Emit a structured JSON result")
    parser.add_argument("hosts", nargs="*")
    return parser


def resolve_publish_reconcile(*, dry_run: bool, requested: bool | None) -> bool:
    if requested is not None:
        return requested
    return not dry_run


def main(argv: list[str] | None = None, *, prog: str = "proxnix-publish") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    config = load_workstation_config(args.config)
    options = PublishOptions(
        dry_run=args.dry_run,
        report_changes=args.report_changes or args.json,
        config_only=args.config_only,
        target_vmid=args.vmid,
        reconcile=resolve_publish_reconcile(dry_run=args.dry_run, requested=args.reconcile),
    )

    if args.container_config is not None:
        options.target_vmid = args.container_config
        options.config_only = True

    output = io.StringIO()
    stream_context = contextlib.redirect_stdout(output) if args.json else contextlib.nullcontext()

    try:
        host_results: list[dict[str, object]] = []
        exit_code = 0
        with stream_context:
            site_paths = need_publish_tools(config, config_only=options.config_only)
            hosts = [*(args.option_hosts or []), *list(args.hosts)] if (args.option_hosts or args.hosts) else list(config.hosts)
            if not hosts:
                raise ProxnixWorkstationError("no publish hosts configured")

            with tempfile.TemporaryDirectory(prefix="proxnix-publish.") as temp_dir:
                temp_root = Path(temp_dir)
                source = materialize_head_site(config, site_paths, temp_root / "site-head", options)
                provider = load_secret_provider(config, source.site_paths)
                if options.target_vmid is not None:
                    validate_target_vmid_repo(
                        config,
                        source.site_paths,
                        provider,
                        options.target_vmid,
                        config_only=options.config_only,
                    )
                elif not options.config_only:
                    validate_site_repo(config, source.site_paths, provider)

                relay_tree = temp_root / "relay"
                build_publish_tree(config, source.site_paths, options, relay_tree)
                write_publish_revision(source, relay_tree / "authority" / "publish-revision.json")

                for host in hosts:
                    with SSHSession(config, host, temp_root=temp_root) as session:
                        if not options.config_only and have_host_relay_private_key(config, provider, source.site_paths):
                            stage_relay_identities_into_tree(config, source.site_paths, options, relay_tree)
                        report = publish_selected_host(session, config, options, relay_tree)
                        host_data = host_report_data(host, report)
                        if options.reconcile:
                            reconcile_result = run_remote_host_api_after_publish(session, options)
                            host_data["reconcile"] = reconcile_result
                            reconcile_exit = int(reconcile_result["exitCode"])
                            if reconcile_exit != 0 and exit_code == 0:
                                exit_code = reconcile_exit
                        host_results.append(host_data)

            if not args.json:
                print("Publish complete" if exit_code == 0 else "Publish complete; host API notification failed")

        if args.json:
            print_json(
                json_ok(
                    {
                        "exitCode": exit_code,
                        "dryRun": options.dry_run,
                        "configOnly": options.config_only,
                        "reconcile": options.reconcile,
                        "vmid": options.target_vmid,
                        "hosts": host_results,
                        "output": output.getvalue().strip(),
                    }
                )
            )
        return exit_code
    except (ConfigError, PlanningError, ProxnixWorkstationError) as exc:
        if args.json:
            print_json(
                json_error(
                    "publish.failed",
                    str(exc),
                    details={"output": output.getvalue().strip()},
                )
            )
        else:
            print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
