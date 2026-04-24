from __future__ import annotations

import argparse
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .config import load_workstation_config
from .errors import ConfigError, PlanningError, ProxnixWorkstationError
from .paths import SitePaths
from .publish_cli import (
    PublishOptions,
    build_publish_tree,
    container_has_any_store,
    need_publish_tools,
    note_remote_file_change,
    print_host_summary,
    should_report_change,
    validate_site_repo,
    validate_target_container_config,
    validate_target_vmid_repo,
)
from .provider_keys import (
    container_private_key_text,
    have_container_private_key,
    have_host_relay_private_key,
    host_relay_private_key_text,
    master_private_key_text,
)
from .publish_cli import do_rsync as publish_do_rsync
from .publish_cli import stage_relay_identities_into_tree
from .secret_provider import (
    EmbeddedSopsProvider,
    SecretProvider,
    container_scope,
    group_scope,
    load_secret_provider,
    shared_scope,
)
from .site import collect_site_vmids, read_container_secret_groups
from .sops_ops import identity_public_key_from_store, public_key_from_private_text
from .ssh_ops import SSHSession


@dataclass
class DoctorReporter:
    warns: int = 0
    fails: int = 0
    lines: list[str] = field(default_factory=list)

    def heading(self, text: str) -> None:
        self.lines.append(f"\n[{text}]")

    def ok(self, text: str) -> None:
        self.lines.append(f"  OK    {text}")

    def info(self, text: str) -> None:
        self.lines.append(f"  INFO  {text}")

    def warn(self, text: str) -> None:
        self.warns += 1
        self.lines.append(f"  WARN  {text}")

    def fail(self, text: str) -> None:
        self.fails += 1
        self.lines.append(f"  FAIL  {text}")

    def render(self) -> str:
        return "\n".join(self.lines).lstrip("\n")

    def exit_code(self) -> int:
        if self.fails > 0:
            return 2
        if self.warns > 0:
            return 1
        return 0


def validate_provider_scope_payload(
    provider: SecretProvider,
    ref,
    label: str,
    reporter: DoctorReporter,
) -> None:
    try:
        data = provider.export_scope(ref)
    except Exception:
        reporter.fail(f"{label} failed to load from secret provider")
        return
    if not isinstance(data, dict):
        reporter.fail(f"{label} has invalid payload shape from secret provider")
        return
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in data.items()):
        reporter.fail(f"{label} has invalid payload shape from secret provider")
        return
    reporter.ok(f"{label} resolves to a flat string map")


def validate_identity_store(config, store: Path, label: str, reporter: DoctorReporter) -> None:
    try:
        identity_public_key_from_store(config, store)
    except Exception:
        reporter.fail(f"{label} failed to decrypt or derive a public key: {store}")
        return
    reporter.ok(f"{label} decrypts and yields a public key")


def validate_private_key_text(private_text: str, label: str, reporter: DoctorReporter) -> None:
    try:
        public_key_from_private_text(private_text, source=label)
    except Exception:
        reporter.fail(f"{label} failed to derive a public key")
        return
    reporter.ok(f"{label} yields a public key")


def check_container_group_file(
    site_paths: SitePaths,
    provider: SecretProvider,
    vmid: str,
    reporter: DoctorReporter,
) -> None:
    path = site_paths.container_secret_groups_file(vmid)
    if not path.is_file():
        return
    try:
        groups = read_container_secret_groups(site_paths, vmid)
    except PlanningError:
        reporter.fail(f"invalid secret group list for container {vmid}: {path}")
        return
    reporter.ok(f"secret group list valid for container {vmid}")
    for group in groups:
        if provider.has_any(group_scope(group)):
            reporter.ok(f"container {vmid} group store present: {group}")
        else:
            reporter.warn(f"container {vmid} references missing group store: {group}")


def check_container_secret_requirements(
    config,
    site_paths: SitePaths,
    provider: SecretProvider,
    vmid: str,
    reporter: DoctorReporter,
) -> None:
    if container_has_any_store(site_paths, provider, vmid):
        if have_container_private_key(config, provider, site_paths, vmid):
            reporter.ok(f"container {vmid} has an identity for compiled secret delivery")
        else:
            reporter.fail(
                f"container {vmid} needs an identity for compiled secret delivery"
            )


def check_expected_tree_dir(path: Path, label: str, reporter: DoctorReporter) -> None:
    if path.is_dir():
        reporter.ok(f"{label} present")
    else:
        reporter.fail(f"{label} missing: {path}")


def lint_site_repo(config, site_paths: SitePaths, options: PublishOptions, reporter: DoctorReporter, temp_root: Path) -> None:
    tree = temp_root / "lint-tree"
    fails_before = reporter.fails
    provider = load_secret_provider(config, site_paths)

    reporter.heading("site")
    reporter.ok("workstation tools and config present")
    reporter.ok(f"site repo present: {site_paths.site_dir}")
    reporter.ok(f"secret backend configured: {provider.describe()}")
    if options.config_only:
        reporter.info("config-only mode skips secret-store prerequisites")
    else:
        try:
            validate_private_key_text(master_private_key_text(config, provider), "master identity", reporter)
        except Exception:
            reporter.fail("master identity is missing or invalid")

    if site_paths.site_nix.is_file():
        reporter.ok("site.nix present")
    else:
        reporter.info("site.nix absent; host baseline will be used without site override")

    if (site_paths.containers_dir / "_template").is_dir():
        reporter.ok("template config present")
    else:
        reporter.info("no containers/_template directory")

    if isinstance(provider, EmbeddedSopsProvider):
        if site_paths.private_dir.is_dir():
            reporter.ok("private site dir present")
        else:
            reporter.info("no private site dir yet")

    if not options.config_only:
        relay_private_text = host_relay_private_key_text(config, provider, site_paths)
        if relay_private_text is not None:
            validate_private_key_text(relay_private_text, "host relay identity", reporter)
        else:
            reporter.warn("host relay identity missing")

        if provider.has_any(shared_scope()):
            validate_provider_scope_payload(provider, shared_scope(), "shared source secrets", reporter)
        else:
            reporter.info("no shared source secrets")

        if isinstance(provider, EmbeddedSopsProvider) and site_paths.shared_identity_store.is_file():
            validate_identity_store(config, site_paths.shared_identity_store, "shared identity store", reporter)

        if isinstance(provider, EmbeddedSopsProvider):
            groups_dir = site_paths.private_dir / "groups"
            if groups_dir.is_dir():
                for store in sorted(groups_dir.rglob("secrets.sops.yaml")):
                    label = f"group store {store.relative_to(groups_dir.parent)}"
                    validate_provider_scope_payload(
                        provider,
                        group_scope(store.parent.name),
                        label,
                        reporter,
                    )

    if options.target_vmid is not None:
        validate_target_vmid_repo(config, site_paths, provider, options.target_vmid, config_only=options.config_only)
        vmids = [options.target_vmid]
    else:
        if not options.config_only:
            validate_site_repo(config, site_paths, provider)
        vmids = collect_site_vmids(site_paths)

    for vmid in vmids:
        check_container_group_file(site_paths, provider, vmid, reporter)
        if not options.config_only:
            check_container_secret_requirements(config, site_paths, provider, vmid, reporter)
            if provider.has_any(container_scope(vmid)):
                validate_provider_scope_payload(
                    provider,
                    container_scope(vmid),
                    f"container {vmid} source secrets",
                    reporter,
                )
            identity_private_text = container_private_key_text(config, provider, site_paths, vmid)
            if identity_private_text is not None:
                validate_private_key_text(identity_private_text, f"container {vmid} identity", reporter)

    if reporter.fails > fails_before:
        reporter.info("skipping compiled publish-tree validation until site errors are fixed")
        return

    build_publish_tree(config, site_paths, options, tree)
    if not options.config_only and have_host_relay_private_key(config, provider, site_paths):
        stage_relay_identities_into_tree(config, site_paths, options, tree)

    if options.target_vmid is not None:
        check_expected_tree_dir(tree / "containers" / options.target_vmid, f"compiled config tree for container {options.target_vmid}", reporter)
        if not options.config_only and (tree / "private" / "containers" / options.target_vmid).is_dir():
            if (tree / "private" / "containers" / options.target_vmid / "effective.sops.yaml").is_file():
                reporter.ok(f"compiled SOPS payload built for container {options.target_vmid}")
            else:
                reporter.info(f"no compiled SOPS payload for container {options.target_vmid}")
            if (tree / "private" / "containers" / options.target_vmid / "age_identity.sops.yaml").is_file():
                reporter.ok(f"relay-encrypted identity staged for container {options.target_vmid}")
    else:
        check_expected_tree_dir(tree / "containers", "compiled config tree", reporter)
        if not options.config_only:
            check_expected_tree_dir(tree / "private" / "containers", "compiled private tree", reporter)
            if (tree / "private" / "host_relay_identity").is_file():
                reporter.ok("host relay identity staged for publish")
            elif have_host_relay_private_key(config, provider, site_paths):
                reporter.fail("host relay identity did not stage into the publish tree")
            else:
                reporter.info("host relay identity not staged because no local store exists")


def compare_host_scope(config, options: PublishOptions, session: SSHSession, report: list[tuple[str, PurePosixPath]], tree: Path) -> None:
    def test_exists(path: PurePosixPath) -> bool:
        return session.run(f"test -e {shlex.quote(str(path))}", check=False).returncode == 0

    if options.target_vmid is not None:
        template_source = tree / "containers" / "_template"
        template_remote = config.remote_dir / "containers" / "_template"
        if template_source.is_dir():
            publish_do_rsync(
                session,
                config,
                template_source,
                template_remote,
                directory_contents=True,
                delete=True,
                dry_run=True,
                report=report,
            )
        elif test_exists(template_remote):
            report.append(("delete", template_remote))

        source_dir = tree / "containers" / options.target_vmid
        remote_dir = config.remote_dir / "containers" / options.target_vmid
        if source_dir.is_dir():
            publish_do_rsync(
                session,
                config,
                source_dir,
                remote_dir,
                directory_contents=True,
                delete=True,
                dry_run=True,
                report=report,
            )
        elif test_exists(remote_dir):
            report.append(("delete", remote_dir))

        if not options.config_only:
            source_private = tree / "private" / "containers" / options.target_vmid
            remote_private = config.remote_priv_dir / "containers" / options.target_vmid
            if source_private.is_dir():
                publish_do_rsync(
                    session,
                    config,
                    source_private,
                    remote_private,
                    directory_contents=True,
                    delete=True,
                    dry_run=True,
                    report=report,
                )
            elif test_exists(remote_private):
                report.append(("delete", remote_private))

            relay_private = tree / "private" / "host_relay_identity"
            if relay_private.is_file():
                publish_do_rsync(session, config, relay_private, config.remote_host_relay_identity, delete=False, dry_run=True, report=report)
            elif test_exists(config.remote_host_relay_identity):
                report.append(("delete", config.remote_host_relay_identity))
        return

    site_nix = tree / "site.nix"
    if site_nix.is_file():
        publish_do_rsync(session, config, site_nix, config.remote_dir / "site.nix", delete=False, dry_run=True, report=report)
    elif test_exists(config.remote_dir / "site.nix"):
        report.append(("delete", config.remote_dir / "site.nix"))

    publish_do_rsync(
        session,
        config,
        tree / "containers",
        config.remote_dir / "containers",
        directory_contents=True,
        delete=True,
        dry_run=True,
        report=report,
    )

    if not options.config_only:
        publish_do_rsync(
            session,
            config,
            tree / "private" / "containers",
            config.remote_priv_dir / "containers",
            directory_contents=True,
            delete=True,
            dry_run=True,
            report=report,
        )
        relay_private = tree / "private" / "host_relay_identity"
        if relay_private.is_file():
            publish_do_rsync(session, config, relay_private, config.remote_host_relay_identity, delete=False, dry_run=True, report=report)
        elif test_exists(config.remote_host_relay_identity):
            report.append(("delete", config.remote_host_relay_identity))


def check_remote_host(config, options: PublishOptions, session: SSHSession, tree: Path, reporter: DoctorReporter) -> None:
    reporter.heading(f"host {session.host}")
    report: list[tuple[str, PurePosixPath]] = []

    if session.run("true", check=False).returncode != 0:
        reporter.fail("SSH connection failed")
        return
    reporter.ok("SSH connection succeeded")

    if session.run(f"test -d {shlex.quote(str(config.remote_dir))}", check=False).returncode != 0:
        reporter.fail(f"remote config dir missing: {config.remote_dir}")
    else:
        reporter.ok(f"remote config dir present: {config.remote_dir}")

    if not options.config_only:
        if session.run(f"test -d {shlex.quote(str(config.remote_priv_dir))}", check=False).returncode != 0:
            reporter.fail(f"remote private dir missing: {config.remote_priv_dir}")
        else:
            reporter.ok(f"remote private dir present: {config.remote_priv_dir}")

    compare_host_scope(config, options, session, report, tree)
    if report:
        reporter.fail("remote relay cache differs from local publish tree")
        for kind, path in report:
            reporter.fail(f"{kind} {path}")
    else:
        reporter.ok("remote relay cache matches local publish tree")


def build_parser(*, prog: str = "proxnix-doctor") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--config", type=Path, help="Path to the proxnix workstation config file")
    parser.add_argument("--site-only", action="store_true")
    parser.add_argument("--host-only", action="store_true")
    parser.add_argument("--config-only", action="store_true")
    parser.add_argument("--vmid")
    parser.add_argument("hosts", nargs="*")
    return parser


def main(argv: list[str] | None = None, *, prog: str = "proxnix-doctor") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    if args.site_only and args.host_only:
        print("error: --site-only and --host-only are mutually exclusive")
        return 2

    config = load_workstation_config(args.config)
    options = PublishOptions(dry_run=True, report_changes=True, config_only=args.config_only, target_vmid=args.vmid)
    reporter = DoctorReporter()

    try:
        if options.target_vmid is not None:
            validate_target_container_config(options.target_vmid)

        site_paths = need_publish_tools(config, config_only=options.config_only)
        with tempfile.TemporaryDirectory(prefix="proxnix-doctor.") as temp_dir:
            temp_root = Path(temp_dir)
            if not args.host_only:
                lint_site_repo(config, site_paths, options, reporter, temp_root)

            if reporter.fails > 0:
                print(reporter.render())
                return reporter.exit_code()

            if args.site_only:
                print(reporter.render())
                return reporter.exit_code()

            hosts = list(args.hosts) if args.hosts else list(config.hosts)
            if not hosts:
                reporter.warn("no hosts configured; skipping remote relay-cache checks")
                print(reporter.render())
                return reporter.exit_code()

            relay_tree = temp_root / "relay"
            build_publish_tree(config, site_paths, options, relay_tree)
            if not options.config_only and have_host_relay_private_key(config, provider, site_paths):
                stage_relay_identities_into_tree(config, site_paths, options, relay_tree)

            for host in hosts:
                with SSHSession(config, host, temp_root=temp_root) as session:
                    check_remote_host(config, options, session, relay_tree, reporter)

        print(reporter.render())
        return reporter.exit_code()
    except (ConfigError, PlanningError, ProxnixWorkstationError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
