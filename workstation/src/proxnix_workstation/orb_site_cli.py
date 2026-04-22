from __future__ import annotations

import argparse
import json
import shutil
import textwrap
from pathlib import Path
from typing import Any

from .config import load_workstation_config
from .errors import ConfigError, ProxnixWorkstationError
from .local_nixos_container_backend import (
    render_nspawn_backend_shell,
    render_prestart_stage_apply_shell,
)
from .orb_exercise_cli import (
    ExerciseContainer,
    RunReport,
    build_generated_config,
    derive_public_key,
    mac_path_to_guest,
    raise_for_completed,
    record_command,
    render_bootstrap_config,
    render_config_file,
    render_fake_pve_conf,
    render_report_files,
    safe_label,
    utc_now,
    write_text,
)
from .paths import SitePaths
from .publish_cli import PublishOptions, build_publish_tree, stage_relay_identities_into_tree, validate_target_vmid_repo
from .provider_keys import have_host_relay_private_key, master_public_key
from .runtime import CommandError, ensure_commands, run_command, shell_join
from .secret_provider import load_secret_provider


DEFAULT_TIMEOUT_SECONDS = 3600
DEFAULT_WORK_DIR = Path(".codex-staging/orb-site")
DEFAULT_MACHINE = "proxnix-local"
DEFAULT_STATE_VERSION = "25.11"
DEFAULT_MEMORY_MB = 2048
DEFAULT_SWAP_MB = 512
DEFAULT_CORES = 2
DEFAULT_DISK_GB = 8
ORB_SITE_NAME_PREFIX = "proxnix-site-orb-guest"

LOCAL_SITE_OVERRIDE = """\
{ lib, ... }:

{
  # Keep the real proxmoxLXC module enabled, but make networking guest-managed
  # so the nested systemd-nspawn guest can configure its own interface.
  proxmoxLXC.manageNetwork = lib.mkForce true;

  # Nested local guests share the OrbStack VM network namespace closely enough
  # that the default SSH socket collides with the host VM listener.
  services.openssh.ports = lib.mkForce [ 2222 ];

  services.qemuGuest.enable = lib.mkDefault true;
}
"""


def build_site_container(vmid: str, hostname: str) -> ExerciseContainer:
    return ExerciseContainer(
        key=f"site-{vmid}",
        vmid=vmid,
        hostname=hostname,
        memory_mb=DEFAULT_MEMORY_MB,
        swap_mb=DEFAULT_SWAP_MB,
        cores=DEFAULT_CORES,
        disk_gb=DEFAULT_DISK_GB,
    )


def default_hostname(machine: str) -> str:
    return machine


def read_root_authorized_keys(paths: list[Path]) -> list[str]:
    keys: list[str] = []
    for path in paths:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#"):
                keys.append(line)
    return keys


def prepare_local_site_tree(source_site_dir: Path, target_site_dir: Path, *, vmid: str) -> None:
    if target_site_dir.exists():
        shutil.rmtree(target_site_dir)
    target_site_dir.mkdir(parents=True, exist_ok=True)

    source_paths = SitePaths(source_site_dir)
    target_paths = SitePaths(target_site_dir)

    if source_paths.site_nix.is_file():
        shutil.copy2(source_paths.site_nix, target_paths.site_nix)

    template_dir = source_paths.containers_dir / "_template"
    if template_dir.is_dir():
        shutil.copytree(template_dir, target_paths.containers_dir / "_template", dirs_exist_ok=True)

    container_dir = source_paths.container_dir(vmid)
    if container_dir.is_dir():
        shutil.copytree(container_dir, target_paths.container_dir(vmid), dirs_exist_ok=True)

    private_roots = [
        source_paths.private_dir / "shared",
        source_paths.private_dir / "groups",
        source_paths.private_dir / "containers" / vmid,
    ]
    for root in private_roots:
        if root.is_dir():
            relative = root.relative_to(source_paths.private_dir)
            shutil.copytree(root, target_paths.private_dir / relative, dirs_exist_ok=True)

    relay_identity_store = source_paths.host_relay_identity_store
    if relay_identity_store.is_file():
        (target_paths.private_dir).mkdir(parents=True, exist_ok=True)
        shutil.copy2(relay_identity_store, target_paths.host_relay_identity_store)

    dropin_dir = target_paths.container_dir(vmid) / "dropins"
    dropin_dir.mkdir(parents=True, exist_ok=True)
    write_text(dropin_dir / "00-proxnix-orb-local.nix", LOCAL_SITE_OVERRIDE)


def build_target_host_tree(config, host_tree: Path, *, vmid: str) -> None:
    if host_tree.exists():
        shutil.rmtree(host_tree)
    site_paths = SitePaths.from_config(config)
    options = PublishOptions(target_vmid=vmid)
    validate_target_vmid_repo(config, site_paths, load_secret_provider(config, site_paths), vmid, config_only=False)
    build_publish_tree(config, site_paths, options, host_tree)
    stage_relay_identities_into_tree(config, site_paths, options, host_tree)


def render_guest_snapshot_script(machine_expr: str) -> str:
    return textwrap.dedent(
        """\
        python3 - %s <<'PY'
import json
import subprocess
import sys

machine = sys.argv[1]

def capture(script: str) -> str:
    completed = subprocess.run(
        [
            "systemd-run",
            "-M",
            machine,
            "--wait",
            "--pipe",
            "--quiet",
            "/run/current-system/sw/bin/bash",
            "-lc",
            script,
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()

def capture_lines(script: str) -> list[str]:
    output = capture(script)
    if not output:
        return []
    return [line for line in output.splitlines() if line]

data = {
    "current_hash": capture("cat /var/lib/proxnix/runtime/current-config-hash"),
    "applied_hash": capture("cat /var/lib/proxnix/runtime/applied-config-hash"),
    "system_state": capture("systemctl is-system-running || true"),
    "failed_units": capture_lines("systemctl --failed --plain --no-legend --no-pager | awk '{print $1}'"),
}
data["hashes_match"] = data["current_hash"] == data["applied_hash"]
print(json.dumps(data, sort_keys=True))
PY
        """
        % machine_expr
    )


def build_remote_site_script(
    *,
    repo_root: Path,
    relay_tree: Path,
    pve_conf_path: Path,
    bootstrap_config_path: Path,
    container: ExerciseContainer,
    timeout_seconds: int,
    keep_running: bool,
) -> str:
    guest_repo_root = mac_path_to_guest(repo_root)
    guest_relay_tree = mac_path_to_guest(relay_tree)
    guest_pve_conf = mac_path_to_guest(pve_conf_path)
    guest_bootstrap_config = mac_path_to_guest(bootstrap_config_path)
    guest_machine = f"{ORB_SITE_NAME_PREFIX}-{container.vmid}"
    nspawn_service_unit = f"proxnix-orb-site-{container.vmid}.service"
    backend_shell = render_nspawn_backend_shell()
    stage_apply_shell = render_prestart_stage_apply_shell()
    guest_snapshot_script = render_guest_snapshot_script('"${GUEST_MACHINE}"')
    keep_running_value = "1" if keep_running else "0"

    return textwrap.dedent(
        f"""\
        #!/run/current-system/sw/bin/bash
        set -euo pipefail

        REPO_ROOT={shlex_quote(guest_repo_root)}
        RELAY_TREE={shlex_quote(guest_relay_tree)}
        PVE_CONF={shlex_quote(guest_pve_conf)}
        BOOTSTRAP_CONFIG={shlex_quote(guest_bootstrap_config)}
        VMID={shlex_quote(container.vmid)}
        GUEST_MACHINE={shlex_quote(guest_machine)}
        NSPAWN_SERVICE_UNIT={shlex_quote(nspawn_service_unit)}
        TIMEOUT_SECONDS={timeout_seconds}
        KEEP_RUNNING={keep_running_value}

        PROXNIX_HOST_DIR="${{REPO_ROOT}}/host"
        PRESTART_HOOK="${{REPO_ROOT}}/host/lxc/hooks/nixos-proxnix-prestart"

        ORB_SITE_ROOT="/var/lib/proxnix-orb-site"
        ORB_STATE_ROOT="${{ORB_SITE_ROOT}}/${{VMID}}"
        RUN_STATE_FILE="${{ORB_STATE_ROOT}}/current-run.json"
        RUN_ROOT=""
        ROOTFS=""
        NSPAWN_LOG=""
        mkdir -p "${{ORB_SITE_ROOT}}"

{backend_shell}
{stage_apply_shell}

        local_site_guest_stop() {{
          systemctl stop "${{NSPAWN_SERVICE_UNIT}}" >/dev/null 2>&1 || true
          machinectl terminate "${{GUEST_MACHINE}}" >/dev/null 2>&1 || true
          systemd-nspawn --cleanup -D "${{ROOTFS}}" -M "${{GUEST_MACHINE}}" >/dev/null 2>&1 || true
          systemctl reset-failed "${{NSPAWN_SERVICE_UNIT}}" >/dev/null 2>&1 || true
          local_nixos_container_cleanup_mounts
        }}

        local_site_guest_start() {{
          local_site_guest_stop
          systemd-run \
            --unit "${{NSPAWN_SERVICE_UNIT}}" \
            --collect \
            --quiet \
            /run/current-system/sw/bin/bash \
            -lc 'exec systemd-nspawn -D "$1" -M "$2" --register=yes /nix/var/nix/profiles/system/init >"$3" 2>&1' \
            _ "${{ROOTFS}}" "${{GUEST_MACHINE}}" "${{NSPAWN_LOG}}" >/dev/null
        }}

        show_failure_logs() {{
          echo "--- nspawn log ---" >&2
          tail -n 200 "${{NSPAWN_LOG}}" >&2 || true
          echo "--- failed units ---" >&2
          systemd-run -M "${{GUEST_MACHINE}}" --wait --pipe --quiet /run/current-system/sw/bin/bash -lc 'systemctl --failed --plain --no-legend --no-pager || true' >&2 || true
          echo "--- proxnix-apply-config journal ---" >&2
          systemd-run -M "${{GUEST_MACHINE}}" --wait --pipe --quiet /run/current-system/sw/bin/journalctl -u proxnix-apply-config.service -b --no-pager -n 120 >&2 || true
        }}

        cleanup_previous_run() {{
          if [ ! -f "${{RUN_STATE_FILE}}" ]; then
            return
          fi

          eval "$(
            python3 - "${{RUN_STATE_FILE}}" <<'PY'
import json
import shlex
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
run_root = payload.get("run_root", "")
print("PREVIOUS_RUN_ROOT=" + shlex.quote(run_root))
PY
          )"

          if [ -n "${{PREVIOUS_RUN_ROOT:-}}" ]; then
            ROOTFS="${{PREVIOUS_RUN_ROOT}}/rootfs"
            NSPAWN_LOG="${{PREVIOUS_RUN_ROOT}}/nspawn.log"
            local_site_guest_stop
            local_nixos_container_clear_immutable
            rm -rf "${{PREVIOUS_RUN_ROOT}}"
          fi

          rm -f "${{RUN_STATE_FILE}}"
        }}

        success=0
        cleanup() {{
          if [ "${{success}}" = "1" ] && [ "${{KEEP_RUNNING}}" = "1" ]; then
            rm -rf "/run/proxnix/${{VMID}}"
            return
          fi

          local_site_guest_stop
          local_nixos_container_clear_immutable
          rm -rf "/run/proxnix/${{VMID}}" "${{RUN_ROOT}}"
          rm -f "${{RUN_STATE_FILE}}"
        }}
        trap cleanup EXIT

        if command -v chattr >/dev/null 2>&1; then
          find "${{ORB_SITE_ROOT}}" -mindepth 2 -maxdepth 2 -type d -name 'run-*' -mmin +10 -exec chattr -R -i {{}} + >/dev/null 2>&1 || true
          chattr -R -i "${{ORB_STATE_ROOT}}" >/dev/null 2>&1 || true
        fi
        find "${{ORB_SITE_ROOT}}" -mindepth 2 -maxdepth 2 -type d -name 'run-*' -mmin +10 -exec rm -rf {{}} +
        cleanup_previous_run

        mkdir -p "${{ORB_STATE_ROOT}}"
        RUN_ROOT="$(mktemp -d "${{ORB_STATE_ROOT}}/run-XXXXXX")"
        chmod 755 "${{RUN_ROOT}}"
        ROOTFS="${{RUN_ROOT}}/rootfs"
        NSPAWN_LOG="${{RUN_ROOT}}/nspawn.log"
        INSTALL_LOG="${{RUN_ROOT}}/install.log"

        mkdir -p /usr/local/lib/proxnix /var/lib/proxnix/private /etc/proxnix
        if ! command -v sops >/dev/null 2>&1; then
          SOPS_PATH="$(nix-build '<nixpkgs>' -A sops --no-out-link)"
          export PATH="${{SOPS_PATH}}/bin:$PATH"
        fi

        install -m 0755 "${{PROXNIX_HOST_DIR}}/pve-conf-to-nix.py" /usr/local/lib/proxnix/pve-conf-to-nix.py
        install -m 0644 "${{PROXNIX_HOST_DIR}}/lxc/hooks/nixos-proxnix-common.sh" /usr/local/lib/proxnix/nixos-proxnix-common.sh
        install -m 0755 "${{PROXNIX_HOST_DIR}}/proxnix-secrets-guest" /usr/local/lib/proxnix/proxnix-secrets-guest

        local_nixos_container_reset_rootfs
        rm -rf /var/lib/proxnix /etc/proxnix "/run/proxnix/${{VMID}}"
        mkdir -p /var/lib/proxnix /var/lib/proxnix/private /etc/proxnix

        install -m 0644 "${{PROXNIX_HOST_DIR}}/base.nix" /var/lib/proxnix/base.nix
        install -m 0644 "${{PROXNIX_HOST_DIR}}/common.nix" /var/lib/proxnix/common.nix
        install -m 0644 "${{PROXNIX_HOST_DIR}}/security-policy.nix" /var/lib/proxnix/security-policy.nix
        install -m 0644 "${{PROXNIX_HOST_DIR}}/configuration.nix" /var/lib/proxnix/configuration.nix
        cp -a "${{RELAY_TREE}}/containers" /var/lib/proxnix/
        if [ -f "${{RELAY_TREE}}/site.nix" ]; then
          install -m 0644 "${{RELAY_TREE}}/site.nix" /var/lib/proxnix/site.nix
        fi
        if [ -d "${{RELAY_TREE}}/private" ]; then
          mkdir -p /var/lib/proxnix/private
          cp -a "${{RELAY_TREE}}/private/." /var/lib/proxnix/private/
        fi
        if [ -f "${{RELAY_TREE}}/private/host_relay_identity" ]; then
          install -m 0600 "${{RELAY_TREE}}/private/host_relay_identity" /etc/proxnix/host_relay_identity
        fi
        chmod 0755 /var/lib/proxnix /var/lib/proxnix/containers
        chmod 0700 /var/lib/proxnix/private /var/lib/proxnix/private/containers /etc/proxnix

        mkdir -p "${{ROOTFS}}/etc/nixos"
        install -m 0644 "${{BOOTSTRAP_CONFIG}}" "${{ROOTFS}}/etc/nixos/configuration.nix"

        SYSTEM_PATH="$(nix-build '<nixpkgs/nixos>' -A system -I nixos-config="${{BOOTSTRAP_CONFIG}}" --no-out-link)"
        if ! nixos-install --root "${{ROOTFS}}" --system "${{SYSTEM_PATH}}" --no-root-password --no-bootloader >"${{INSTALL_LOG}}" 2>&1; then
          echo "--- install log ---" >&2
          tail -n 200 "${{INSTALL_LOG}}" >&2 || true
          exit 1
        fi
        ROOT_CHANNELS="${{ROOTFS}}/nix/var/nix/profiles/per-user/root/channels"
        HOST_NIXPKGS="$(nix-instantiate --find-file nixpkgs)"
        rm -rf "${{ROOT_CHANNELS}}"
        mkdir -p "${{ROOT_CHANNELS}}"
        cp -a --reflink=auto "${{HOST_NIXPKGS}}" "${{ROOT_CHANNELS}}/nixos"
        cp -a --reflink=auto "${{HOST_NIXPKGS}}" "${{ROOT_CHANNELS}}/nixpkgs-unstable"
        if ! chroot "${{ROOTFS}}" /nix/var/nix/profiles/system/activate >>"${{INSTALL_LOG}}" 2>&1; then
          echo "--- install log ---" >&2
          tail -n 200 "${{INSTALL_LOG}}" >&2 || true
          exit 1
        fi
        local_nixos_container_cleanup_mounts

        bash "${{PRESTART_HOOK}}" --vmid "${{VMID}}" --pve-conf "${{PVE_CONF}}"
        local_nixos_container_apply_prestart_stage "/run/proxnix/${{VMID}}"
        local_nixos_container_prepare_runtime_tree

        local_site_guest_start
        local_nixos_container_wait_until_ready

        apply_state=""
        for _ in $(seq 1 "${{TIMEOUT_SECONDS}}"); do
          current=""
          applied=""
          if [ -r "${{ROOTFS}}/var/lib/proxnix/runtime/current-config-hash" ]; then
            IFS= read -r current < "${{ROOTFS}}/var/lib/proxnix/runtime/current-config-hash" || true
          fi
          if [ -r "${{ROOTFS}}/var/lib/proxnix/runtime/applied-config-hash" ]; then
            IFS= read -r applied < "${{ROOTFS}}/var/lib/proxnix/runtime/applied-config-hash" || true
          fi
          if [ -n "$current" ] && [ "$current" = "$applied" ]; then
            apply_state="ready"
            break
          fi
          if [ -d "${{ROOTFS}}/var/log/journal" ] && journalctl --directory="${{ROOTFS}}/var/log/journal" -u proxnix-apply-config.service -b --no-pager -n 120 | grep -q "Failed to start Apply proxnix-managed NixOS config after boot."; then
            apply_state="apply-failed"
            show_failure_logs
            exit 1
          fi
          sleep 1
        done

        if [ "${{apply_state}}" != "ready" ]; then
          show_failure_logs
          exit 1
        fi

        local_site_guest_stop
        local_site_guest_start
        local_nixos_container_wait_until_ready

        guest_snapshot="$(
{guest_snapshot_script.rstrip()}
        )"

        result_json="$(
          python3 - "${{guest_snapshot}}" "${{GUEST_MACHINE}}" "${{RUN_ROOT}}" "${{KEEP_RUNNING}}" <<'PY'
import json
import sys

guest_snapshot = json.loads(sys.argv[1])
payload = {{
    "guest_machine": sys.argv[2],
    "run_root": sys.argv[3],
    "keep_running": sys.argv[4] == "1",
}}
payload.update(guest_snapshot)
print(json.dumps(payload, sort_keys=True))
PY
        )"

        if [ "$(python3 - "${{result_json}}" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
print("1" if payload.get("failed_units") else "0")
PY
)" = "1" ]; then
          show_failure_logs
          printf '%s\n' "${{result_json}}" >&2
          exit 1
        fi

        success=1
        if [ "${{KEEP_RUNNING}}" = "1" ]; then
          python3 - "${{RUN_STATE_FILE}}" "${{result_json}}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(sys.argv[2])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
PY
        else
          local_site_guest_stop
          local_nixos_container_clear_immutable
          rm -rf "${{RUN_ROOT}}"
          rm -f "${{RUN_STATE_FILE}}"
        fi

        rm -rf "/run/proxnix/${{VMID}}"
        trap - EXIT
        printf '%s\n' "${{result_json}}"
        """
    )


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def run_orb_site(
    report: RunReport,
    artifacts_dir: Path,
    *,
    machine: str,
    script_text: str,
) -> dict[str, Any]:
    script_path = artifacts_dir / f"{safe_label(machine)}-orb-site.sh"
    write_text(script_path, script_text, mode=0o755)
    completed = run_command(
        ["orbctl", "run", "-m", machine, "-u", "root", "bash", mac_path_to_guest(script_path)],
        check=False,
        capture_output=True,
    )
    command = shell_join(["orbctl", "run", "-m", machine, "-u", "root", "bash", mac_path_to_guest(script_path)])
    record_command(report, artifacts_dir, "orb-site-run", command, completed)
    raise_for_completed("orbctl run orb site", completed)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProxnixWorkstationError("orb site run returned invalid JSON") from exc


def build_parser(*, prog: str = "proxnix exercise orb-site") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--config", type=Path, help="Path to the proxnix workstation config file")
    parser.add_argument("--machine", default=DEFAULT_MACHINE, help=f"OrbStack machine name (default: {DEFAULT_MACHINE})")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--vmid", required=True, help="Container VMID from the site repo to boot locally")
    parser.add_argument("--hostname", help="Hostname exposed through the synthetic PVE config (default: machine name)")
    parser.add_argument("--state-version", default=DEFAULT_STATE_VERSION)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--nameserver", action="append", default=[], help="Synthetic PVE nameserver entry; can be repeated")
    parser.add_argument("--search-domain", help="Synthetic PVE search domain")
    parser.add_argument(
        "--root-authorized-key",
        action="append",
        type=Path,
        default=None,
        help="Public key file to inject as synthetic PVE root authorized_keys; can be repeated",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Tear the nested guest down after validation instead of leaving it running",
    )
    return parser


def main(argv: list[str] | None = None, *, prog: str = "proxnix exercise orb-site") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)

    started_at = utc_now()
    report = RunReport(started_at=started_at)
    work_root = args.work_dir.expanduser().resolve() / f"vmid-{args.vmid}"
    report_dir = work_root / "reports" / "latest"

    try:
        ensure_commands(["orbctl", "sops", "ssh-keygen"])
        source_config = load_workstation_config(args.config)
        source_site_dir = source_config.require_site_dir()

        artifacts_dir = report_dir / "artifacts"
        local_site_dir = work_root / "site"
        host_tree = work_root / "host-tree"
        generated_config_path = work_root / "workstation-config"
        pve_conf_path = work_root / "fake-pve.conf"
        bootstrap_config_path = work_root / "bootstrap-configuration.nix"
        repo_root = Path(__file__).resolve().parents[3]

        if args.hostname is None:
            args.hostname = default_hostname(args.machine)

        if report_dir.exists():
            shutil.rmtree(report_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        container = build_site_container(args.vmid, args.hostname)
        prepare_local_site_tree(source_site_dir, local_site_dir, vmid=args.vmid)

        generated_config = build_generated_config(
            source_config,
            config_path=generated_config_path,
            site_dir=local_site_dir,
            host=args.machine,
        )
        write_text(generated_config_path, render_config_file(generated_config))
        build_target_host_tree(generated_config, host_tree, vmid=args.vmid)

        if args.root_authorized_key:
            keys = read_root_authorized_keys([path.expanduser() for path in args.root_authorized_key])
            root_public_key = "\n".join(keys).strip()
        else:
            if source_config.ssh_identity is not None:
                root_public_key = derive_public_key(source_config.ssh_identity)
            else:
                root_public_key = master_public_key(
                    source_config,
                    load_secret_provider(source_config, SitePaths.from_config(source_config)),
                )
        render_fake_pve_conf(
            pve_conf_path,
            container=container,
            root_public_key=root_public_key,
            nameservers=args.nameserver,
            search_domain=args.search_domain,
        )
        render_bootstrap_config(bootstrap_config_path, state_version=args.state_version)

        report.host = args.machine
        report.work_dir = str(work_root)
        report.report_dir = str(report_dir)
        report.config_path = str(generated_config_path)
        report.site_dir = str(source_site_dir)
        report.containers = [
            {
                "key": container.key,
                "vmid": container.vmid,
                "hostname": container.hostname,
                "guest_machine": f"{ORB_SITE_NAME_PREFIX}-{container.vmid}",
            }
        ]
        report.capabilities = {
            "real-prestart-hook": True,
            "backend-stage-apply": True,
            "nested-nixos-guest": True,
            "proxmoxlxc-enabled": True,
            "local-network-override": True,
            "site-repo-input": True,
            "keep-running": not args.cleanup,
        }

        script_text = build_remote_site_script(
            repo_root=repo_root,
            relay_tree=host_tree,
            pve_conf_path=pve_conf_path,
            bootstrap_config_path=bootstrap_config_path,
            container=container,
            timeout_seconds=args.timeout_seconds,
            keep_running=not args.cleanup,
        )
        result = run_orb_site(report, artifacts_dir, machine=args.machine, script_text=script_text)

        report.add_assertion(
            container.key,
            "managed config applied",
            bool(result.get("hashes_match")),
            f"current={result.get('current_hash')!r} applied={result.get('applied_hash')!r}",
        )
        failed_units = result.get("failed_units")
        failed_unit_list = failed_units if isinstance(failed_units, list) else []
        report.add_assertion(
            container.key,
            "no failed guest units",
            not failed_unit_list,
            "failed=" + (", ".join(failed_unit_list) if failed_unit_list else "none"),
        )
        report.add_assertion(
            container.key,
            "guest left running",
            bool(result.get("keep_running")) == (not args.cleanup),
            f"keep_running={result.get('keep_running')!r} cleanup={args.cleanup!r}",
        )

        if report.status == "running":
            report.status = "passed"
        report.finished_at = utc_now()
        render_report_files(report, report_dir)

        print(f"Orb site container ready. Report: {report_dir / 'report.md'}")
        print(f"Guest machine: {result.get('guest_machine')}")
        if not args.cleanup:
            print(f"Orb VM shell: orb -m {args.machine}")
            print(f"Guest shell in Orb VM: machinectl shell {result.get('guest_machine')}")
        return 0 if report.status == "passed" else 1
    except (ConfigError, ProxnixWorkstationError, CommandError) as exc:
        report.add_error(str(exc))
        report.finished_at = utc_now()
        render_report_files(report, Path(report.report_dir) if report.report_dir else report_dir)
        print(f"error: {exc}")
        if report.report_dir:
            print(f"Partial report: {Path(report.report_dir) / 'report.md'}")
        return 1
