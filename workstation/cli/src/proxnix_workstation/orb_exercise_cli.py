from __future__ import annotations

import argparse
import json
import shlex
import textwrap
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

from .config import load_workstation_config
from .errors import ConfigError, ProxnixWorkstationError
from .local_nixos_container_backend import (
    render_nspawn_backend_shell,
    render_prestart_stage_apply_shell,
)
from .paths import SitePaths
from .provider_keys import master_public_key
from .runtime import CommandError, ensure_commands, run_command, shell_join
from .secret_provider import load_secret_provider
from .test_site_fixture import (
    STATUS_PORT,
    build_expected_assertions,
    render_test_site,
    seed_test_site_secrets,
)


DEFAULT_TIMEOUT_SECONDS = 3600
DEFAULT_WORK_DIR = Path(".codex-staging/orb-exercise")
DEFAULT_MACHINE = "proxnix-local"
DEFAULT_STATE_VERSION = "25.11"
ORB_NSPAWN_NAME_PREFIX = "proxnix-orb-guest"


BOOTSTRAP_CONFIG = """\
{ ... }: {
  boot.isContainer = true;
  networking.hostName = "proxnix-orb-bootstrap";
  nix.settings.sandbox = false;
  system.stateVersion = "__STATE_VERSION__";
}
"""


@dataclass(frozen=True)
class ExerciseContainer:
    key: str
    vmid: str
    hostname: str
    memory_mb: int
    swap_mb: int
    cores: int
    disk_gb: int
    secret_groups: tuple[str, ...] = ()


@dataclass
class AssertionResult:
    scope: str
    name: str
    status: str
    detail: str


@dataclass
class CommandRecord:
    label: str
    command: str
    returncode: int
    stdout_path: str | None
    stderr_path: str | None


@dataclass
class RunReport:
    started_at: str
    finished_at: str | None = None
    status: str = "running"
    host: str = ""
    work_dir: str = ""
    report_dir: str = ""
    config_path: str = ""
    site_dir: str = ""
    containers: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)
    assertions: list[AssertionResult] = field(default_factory=list)
    commands: list[CommandRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_assertion(self, scope: str, name: str, ok: bool, detail: str) -> None:
        self.assertions.append(
            AssertionResult(
                scope=scope,
                name=name,
                status="passed" if ok else "failed",
                detail=detail,
            )
        )
        if not ok:
            self.status = "failed"

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.status = "failed"

    def summary(self) -> dict[str, int]:
        passed = sum(1 for item in self.assertions if item.status == "passed")
        failed = sum(1 for item in self.assertions if item.status == "failed")
        return {
            "passed": passed,
            "failed": failed,
            "total": len(self.assertions),
        }

    def to_json_data(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "host": self.host,
            "work_dir": self.work_dir,
            "report_dir": self.report_dir,
            "config_path": self.config_path,
            "site_dir": self.site_dir,
            "containers": self.containers,
            "capabilities": self.capabilities,
            "summary": self.summary(),
            "assertions": [asdict(item) for item in self.assertions],
            "commands": [asdict(item) for item in self.commands],
            "errors": list(self.errors),
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_text(path: Path, text: str, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def safe_label(label: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in label).strip("-").lower() or "command"


def stderr_or_stdout(completed: CompletedProcess[str]) -> str:
    return (completed.stderr or completed.stdout or "").strip()


def raise_for_completed(command: str, completed: CompletedProcess[str]) -> None:
    if completed.returncode == 0:
        return
    suffix = stderr_or_stdout(completed)
    if suffix:
        raise CommandError(f"{command} failed with exit code {completed.returncode}: {suffix}")
    raise CommandError(f"{command} failed with exit code {completed.returncode}")


def record_command(
    report: RunReport,
    artifacts_dir: Path,
    label: str,
    command: str,
    completed: CompletedProcess[str],
) -> CompletedProcess[str]:
    index = len(report.commands) + 1
    base = f"{index:02d}-{safe_label(label)}"
    stdout_path: Path | None = None
    stderr_path: Path | None = None

    if completed.stdout:
        stdout_path = artifacts_dir / f"{base}.stdout.log"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
    if completed.stderr:
        stderr_path = artifacts_dir / f"{base}.stderr.log"
        stderr_path.write_text(completed.stderr, encoding="utf-8")

    report.commands.append(
        CommandRecord(
            label=label,
            command=command,
            returncode=completed.returncode,
            stdout_path=None if stdout_path is None else str(stdout_path),
            stderr_path=None if stderr_path is None else str(stderr_path),
        )
    )
    return completed


def run_logged_local_command(
    report: RunReport,
    artifacts_dir: Path,
    label: str,
    args: list[str],
    *,
    check: bool = True,
) -> CompletedProcess[str]:
    completed = run_command(args, check=False, capture_output=True)
    command = shell_join(args)
    record_command(report, artifacts_dir, label, command, completed)
    if check:
        raise_for_completed(command, completed)
    return completed


def render_markdown_report(report: RunReport) -> str:
    summary = report.summary()
    lines = [
        "# proxnix OrbStack probe report",
        "",
        f"- Status: `{report.status}`",
        f"- Host: `{report.host}`",
        f"- Started: `{report.started_at}`",
        f"- Finished: `{report.finished_at}`",
        f"- Work dir: `{report.work_dir}`",
        f"- Site dir: `{report.site_dir}`",
        "",
        "## Assertions",
        "",
        f"- Passed: `{summary['passed']}`",
        f"- Failed: `{summary['failed']}`",
        f"- Total: `{summary['total']}`",
        "",
    ]
    for item in report.assertions:
        marker = "PASS" if item.status == "passed" else "FAIL"
        lines.append(f"- `{marker}` `{item.scope}` `{item.name}`: {item.detail}")
    if report.errors:
        lines.extend(["", "## Errors", ""])
        for message in report.errors:
            lines.append(f"- {message}")
    lines.extend(["", "## Command logs", ""])
    for command in report.commands:
        details = [f"rc={command.returncode}"]
        if command.stdout_path is not None:
            details.append(f"stdout={command.stdout_path}")
        if command.stderr_path is not None:
            details.append(f"stderr={command.stderr_path}")
        lines.append(f"- `{command.label}`: `{command.command}` ({', '.join(details)})")
    return "\n".join(lines) + "\n"


def render_report_files(report: RunReport, report_dir: Path) -> None:
    write_text(report_dir / "report.json", json.dumps(report.to_json_data(), indent=2, sort_keys=True) + "\n")
    write_text(report_dir / "report.md", render_markdown_report(report))


def derive_public_key(identity_path: Path) -> str:
    public_path = identity_path.with_suffix(identity_path.suffix + ".pub")
    if public_path.is_file():
        return public_path.read_text(encoding="utf-8").strip()

    completed = run_command(
        ["ssh-keygen", "-y", "-f", str(identity_path)],
        check=False,
        capture_output=True,
    )
    raise_for_completed(shell_join(["ssh-keygen", "-y", "-f", str(identity_path)]), completed)
    return completed.stdout.strip()


def build_generated_config(
    source,
    *,
    config_path: Path,
    site_dir: Path,
    host: str,
):
    from .config import WorkstationConfig

    return WorkstationConfig(
        config_file=config_path,
        site_dir=site_dir,
        hosts=(host,),
        ssh_identity=source.ssh_identity,
        remote_dir=source.remote_dir,
        remote_priv_dir=source.remote_priv_dir,
        remote_host_relay_identity=source.remote_host_relay_identity,
        secret_provider=source.secret_provider,
        secret_provider_command=source.secret_provider_command,
        scripts_dir=source.scripts_dir,
        provider_environment=source.provider_environment,
    )


def render_config_file(config) -> str:
    lines = [
        f"PROXNIX_SITE_DIR={shlex.quote(str(config.site_dir))}",
        f"PROXNIX_HOSTS={shlex.quote(' '.join(config.hosts))}",
        f"PROXNIX_REMOTE_DIR={shlex.quote(str(config.remote_dir))}",
        f"PROXNIX_REMOTE_PRIV_DIR={shlex.quote(str(config.remote_priv_dir))}",
        f"PROXNIX_REMOTE_HOST_RELAY_IDENTITY={shlex.quote(str(config.remote_host_relay_identity))}",
        f"PROXNIX_SECRET_PROVIDER={shlex.quote(config.secret_provider)}",
    ]
    if config.secret_provider_command is not None:
        lines.append(f"PROXNIX_SECRET_PROVIDER_COMMAND={shlex.quote(config.secret_provider_command)}")
    if config.ssh_identity is not None:
        lines.append(f"PROXNIX_SSH_IDENTITY={shlex.quote(str(config.ssh_identity))}")
    if config.scripts_dir is not None:
        lines.append(f"PROXNIX_SCRIPTS_DIR={shlex.quote(str(config.scripts_dir))}")
    for key, value in config.provider_environment:
        lines.append(f"{key}={shlex.quote(value)}")
    return "\n".join(lines) + "\n"


def build_host_tree(config, host_tree: Path) -> None:
    from .publish_cli import PublishOptions, build_publish_tree, stage_relay_identities_into_tree

    if host_tree.exists():
        import shutil

        shutil.rmtree(host_tree)
    site_paths = SitePaths.from_config(config)
    options = PublishOptions()
    build_publish_tree(config, site_paths, options, host_tree)
    stage_relay_identities_into_tree(config, site_paths, options, host_tree)


def build_probe_container(vmid: str) -> ExerciseContainer:
    return ExerciseContainer(
        key="orb-probe",
        vmid=vmid,
        hostname="proxnix-orb-probe",
        memory_mb=2048,
        swap_mb=512,
        cores=2,
        disk_gb=8,
        secret_groups=("exercise-group", "podman-group"),
    )


def render_fake_pve_conf(
    path: Path,
    *,
    container: ExerciseContainer,
    root_public_key: str,
    nameservers: list[str] | None = None,
    search_domain: str | None = None,
) -> None:
    ssh_value = urllib.parse.quote(root_public_key + "\n", safe="")
    lines = [
        "arch: amd64",
        f"hostname: {container.hostname}",
        "ostype: unmanaged",
        f"cores: {container.cores}",
        f"memory: {container.memory_mb}",
        f"swap: {container.swap_mb}",
        f"rootfs: local-lvm:vm-{container.vmid}-disk-0,size={container.disk_gb}G",
        "unprivileged: 0",
        "net0: name=eth0,bridge=vmbr0,ip=dhcp",
        f"ssh-public-keys: {ssh_value}",
    ]
    if nameservers:
        lines.append(f"nameserver: {' '.join(nameservers)}")
    if search_domain:
        lines.append(f"searchdomain: {search_domain}")
    write_text(path, "\n".join(lines) + "\n")


def render_bootstrap_config(path: Path, *, state_version: str) -> None:
    write_text(path, BOOTSTRAP_CONFIG.replace("__STATE_VERSION__", state_version))


def mac_path_to_guest(path: Path) -> str:
    return "/mnt/mac" + str(path.resolve())


def build_remote_probe_script(
    *,
    repo_root: Path,
    relay_tree: Path,
    pve_conf_path: Path,
    bootstrap_config_path: Path,
    container: ExerciseContainer,
    timeout_seconds: int,
) -> str:
    guest_repo_root = mac_path_to_guest(repo_root)
    guest_relay_tree = mac_path_to_guest(relay_tree)
    guest_pve_conf = mac_path_to_guest(pve_conf_path)
    guest_bootstrap_config = mac_path_to_guest(bootstrap_config_path)
    guest_machine = f"{ORB_NSPAWN_NAME_PREFIX}-{container.vmid}"
    backend_shell = render_nspawn_backend_shell()
    stage_apply_shell = render_prestart_stage_apply_shell()

    return textwrap.dedent(
        f"""\
        #!/run/current-system/sw/bin/bash
        set -euo pipefail

        REPO_ROOT={shlex.quote(guest_repo_root)}
        RELAY_TREE={shlex.quote(guest_relay_tree)}
        PVE_CONF={shlex.quote(guest_pve_conf)}
        BOOTSTRAP_CONFIG={shlex.quote(guest_bootstrap_config)}
        VMID={shlex.quote(container.vmid)}
        GUEST_MACHINE={shlex.quote(guest_machine)}
        TIMEOUT_SECONDS={timeout_seconds}

        PROXNIX_HOST_DIR="${{REPO_ROOT}}/host"
        PRESTART_HOOK="${{REPO_ROOT}}/host/runtime/lxc/hooks/nixos-proxnix-prestart"

        ORB_EXERCISE_ROOT="/var/lib/proxnix-orb-exercise"
        ORB_STATE_ROOT="${{ORB_EXERCISE_ROOT}}/${{VMID}}"
        mkdir -p "${{ORB_EXERCISE_ROOT}}"
        if command -v chattr >/dev/null 2>&1; then
          find "${{ORB_EXERCISE_ROOT}}" -mindepth 2 -maxdepth 2 -type d -name 'run-*' -mmin +10 -exec chattr -R -i {{}} + >/dev/null 2>&1 || true
          chattr -R -i "${{ORB_STATE_ROOT}}" >/dev/null 2>&1 || true
        fi
        find "${{ORB_EXERCISE_ROOT}}" -mindepth 2 -maxdepth 2 -type d -name 'run-*' -mmin +10 -exec rm -rf {{}} +
        rm -rf "${{ORB_STATE_ROOT}}"
        mkdir -p "${{ORB_STATE_ROOT}}"
        RUN_ROOT="$(mktemp -d "${{ORB_STATE_ROOT}}/run-XXXXXX")"
        chmod 755 "${{RUN_ROOT}}"
        ROOTFS="${{RUN_ROOT}}/rootfs"
        NSPAWN_LOG="${{RUN_ROOT}}/nspawn.log"
        INSTALL_LOG="${{RUN_ROOT}}/install.log"

{backend_shell}
{stage_apply_shell}

        show_install_log() {{
          echo "--- install log ---" >&2
          tail -n 200 "${{INSTALL_LOG}}" >&2 || true
        }}

        cleanup() {{
          local_nixos_container_stop
          local_nixos_container_clear_immutable
          rm -rf "/run/proxnix/${{VMID}}" "${{RUN_ROOT}}"
        }}
        trap cleanup EXIT

        mkdir -p /usr/local/lib/proxnix /var/lib/proxnix/private /etc/proxnix "${{ORB_STATE_ROOT}}"
        if ! command -v sops >/dev/null 2>&1; then
          SOPS_PATH="$(nix-build '<nixpkgs>' -A sops --no-out-link)"
          export PATH="${{SOPS_PATH}}/bin:$PATH"
        fi

        install -m 0755 "${{PROXNIX_HOST_DIR}}/runtime/lib/pve-conf-to-nix.py" /usr/local/lib/proxnix/pve-conf-to-nix.py
        install -m 0644 "${{PROXNIX_HOST_DIR}}/runtime/lxc/hooks/nixos-proxnix-common.sh" /usr/local/lib/proxnix/nixos-proxnix-common.sh
        install -m 0755 "${{PROXNIX_HOST_DIR}}/runtime/lib/proxnix-secrets-guest" /usr/local/lib/proxnix/proxnix-secrets-guest

        local_nixos_container_reset_rootfs
        rm -rf /var/lib/proxnix /etc/proxnix "/run/proxnix/${{VMID}}"
        mkdir -p /var/lib/proxnix /var/lib/proxnix/private /etc/proxnix

        install -m 0644 "${{PROXNIX_HOST_DIR}}/runtime/nix/base.nix" /var/lib/proxnix/base.nix
        install -m 0644 "${{PROXNIX_HOST_DIR}}/runtime/nix/common.nix" /var/lib/proxnix/common.nix
        install -m 0644 "${{PROXNIX_HOST_DIR}}/runtime/nix/security-policy.nix" /var/lib/proxnix/security-policy.nix
        install -m 0644 "${{PROXNIX_HOST_DIR}}/runtime/nix/configuration.nix" /var/lib/proxnix/configuration.nix
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
          show_install_log
          exit 1
        fi
        ROOT_CHANNELS="${{ROOTFS}}/nix/var/nix/profiles/per-user/root/channels"
        HOST_NIXPKGS="$(nix-instantiate --find-file nixpkgs)"
        rm -rf "${{ROOT_CHANNELS}}"
        mkdir -p "${{ROOT_CHANNELS}}"
        cp -a --reflink=auto "${{HOST_NIXPKGS}}" "${{ROOT_CHANNELS}}/nixos"
        cp -a --reflink=auto "${{HOST_NIXPKGS}}" "${{ROOT_CHANNELS}}/nixpkgs-unstable"
        if ! chroot "${{ROOTFS}}" /nix/var/nix/profiles/system/activate >>"${{INSTALL_LOG}}" 2>&1; then
          show_install_log
          exit 1
        fi
        local_nixos_container_cleanup_mounts

        bash "${{PRESTART_HOOK}}" --vmid "${{VMID}}" --pve-conf "${{PVE_CONF}}"
        local_nixos_container_apply_prestart_stage "/run/proxnix/${{VMID}}"
        local_nixos_container_prepare_runtime_tree

        local_nixos_container_start
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
            exit 1
          fi
          sleep 1
        done

        if [ "${{apply_state}}" != "ready" ]; then
          echo "--- nspawn log ---" >&2
          tail -n 200 "${{NSPAWN_LOG}}" >&2 || true
          echo "--- proxnix-apply-config journal ---" >&2
          systemd-run -M "${{GUEST_MACHINE}}" --wait --pipe --quiet /run/current-system/sw/bin/journalctl -u proxnix-apply-config.service -b --no-pager -n 120 >&2 || true
          echo "--- test-site status journal ---" >&2
          systemd-run -M "${{GUEST_MACHINE}}" --wait --pipe --quiet /run/current-system/sw/bin/journalctl -u proxnix-test-site-status.service -b --no-pager -n 120 >&2 || true
          exit 1
        fi

        local_nixos_container_stop
        local_nixos_container_start
        local_nixos_container_wait_until_ready

        status_output=""
        status_state=""
        for _ in $(seq 1 "${{TIMEOUT_SECONDS}}"); do
          set +e
          status_output="$(
            systemd-run -M "${{GUEST_MACHINE}}" --wait --pipe --quiet /run/current-system/sw/bin/bash -lc '
              systemctl start proxnix-test-site-status.service >/dev/null 2>&1 || true
              for unit in \
                proxnix-test-site-status.service
              do
                if systemctl is-failed --quiet "$unit"; then
                  printf "status-failed\\n"
                  journalctl -u "$unit" -b --no-pager -n 120 >&2 || true
                  exit 0
                fi
              done
              if [ -f /var/lib/proxnix-test-site/www/status.json ]; then
                podman_state="$(systemctl show -p ActiveState --value proxnix-test-site-podman.service || true)"
                if [ "$podman_state" = "activating" ]; then
                  printf "waiting\\n"
                  exit 0
                fi
                printf "ready\\n"
                exit 0
              fi
              printf "waiting\\n"
              exit 0
            ' 2>&1
          )"
          status_state="${{status_output%%$'\\n'*}}"
          status=$?
          set -e
          if [ "$status" -eq 0 ] && [ "${{status_state}}" = "ready" ]; then
            break
          fi
          if [ "$status" -eq 0 ] && [ "${{status_state}}" = "status-failed" ]; then
            printf '%s\n' "${{status_output}}" >&2
            exit 1
          fi
          sleep 1
        done

        if [ "${{status_state}}" != "ready" ]; then
          echo "--- nspawn log ---" >&2
          tail -n 200 "${{NSPAWN_LOG}}" >&2 || true
          echo "--- test-site status journal ---" >&2
          systemd-run -M "${{GUEST_MACHINE}}" --wait --pipe --quiet /run/current-system/sw/bin/journalctl -u proxnix-test-site-status.service -b --no-pager -n 120 >&2 || true
          echo "--- podman journal ---" >&2
          systemd-run -M "${{GUEST_MACHINE}}" --wait --pipe --quiet /run/current-system/sw/bin/journalctl -u proxnix-test-site-podman.service -b --no-pager -n 120 >&2 || true
          exit 1
        fi

        result_json="$(
          python3 - "${{GUEST_MACHINE}}" <<'PY'
import json
import subprocess
import sys
import urllib.request

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

current = capture("cat /var/lib/proxnix/runtime/current-config-hash")
applied = capture("cat /var/lib/proxnix/runtime/applied-config-hash")
status_file = json.loads(capture("cat /var/lib/proxnix-test-site/www/status.json"))
status_http = json.loads(
    capture(
        '''python3 - <<'EOF'
import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:%d/status.json", timeout=5).read().decode("utf-8"))
EOF''' % {STATUS_PORT}
    )
)

data = {{
    "guest_machine": machine,
    "current_hash": current,
    "applied_hash": applied,
    "hashes_match": current == applied,
    "status_file": status_file,
    "status_http": status_http,
    "status_http_matches_file": status_http == status_file,
}}
print(json.dumps(data, sort_keys=True))
PY
        )"

        local_nixos_container_stop
        rm -rf "/run/proxnix/${{VMID}}"
        trap - EXIT
        printf '%s\n' "${{result_json}}"
        """
    )


def run_orb_probe(
    report: RunReport,
    artifacts_dir: Path,
    *,
    machine: str,
    script_text: str,
) -> dict[str, Any]:
    script_path = artifacts_dir / f"{safe_label(machine)}-orb-probe.sh"
    write_text(script_path, script_text, mode=0o755)
    completed = run_logged_local_command(
        report,
        artifacts_dir,
        "orb-probe-run",
        ["orbctl", "run", "-m", machine, "-u", "root", "bash", mac_path_to_guest(script_path)],
        check=False,
    )
    raise_for_completed("orbctl run orb probe", completed)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProxnixWorkstationError("orb probe returned invalid JSON") from exc


def build_parser(*, prog: str = "proxnix exercise orb-probe") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--config", type=Path, help="Path to the proxnix workstation config file")
    parser.add_argument("--machine", default=DEFAULT_MACHINE, help=f"OrbStack machine name (default: {DEFAULT_MACHINE})")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--vmid", default="9940", help="Synthetic VMID for the local nested guest")
    parser.add_argument("--state-version", default=DEFAULT_STATE_VERSION)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return parser


def main(argv: list[str] | None = None, *, prog: str = "proxnix exercise orb-probe") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)

    started_at = utc_now()
    report = RunReport(started_at=started_at)
    work_dir = args.work_dir.expanduser().resolve()
    report_dir = work_dir / "reports" / "latest"

    try:
        ensure_commands(["orbctl", "sops", "ssh-keygen"])
        source_config = load_workstation_config(args.config)

        artifacts_dir = report_dir / "artifacts"
        site_dir = work_dir / "site"
        host_tree = work_dir / "host-tree"
        generated_config_path = work_dir / "workstation-config"
        pve_conf_path = work_dir / "fake-pve.conf"
        bootstrap_config_path = work_dir / "bootstrap-configuration.nix"
        repo_root = Path(__file__).resolve().parents[3]
        token = f"orb-{args.vmid}"

        if report_dir.exists():
            import shutil

            shutil.rmtree(report_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        container = build_probe_container(args.vmid)
        generated_config = build_generated_config(
            source_config,
            config_path=generated_config_path,
            site_dir=site_dir,
            host=args.machine,
        )
        write_text(generated_config_path, render_config_file(generated_config))
        render_test_site(site_dir, vmid=container.vmid, token=token)
        seed_test_site_secrets(generated_config, vmid=container.vmid, token=token)
        build_host_tree(generated_config, host_tree)

        if source_config.ssh_identity is not None:
            public_key = derive_public_key(source_config.ssh_identity)
        else:
            public_key = master_public_key(source_config, load_secret_provider(source_config, SitePaths.from_config(source_config)))
        render_fake_pve_conf(pve_conf_path, container=container, root_public_key=public_key)
        render_bootstrap_config(bootstrap_config_path, state_version=args.state_version)

        report.host = args.machine
        report.work_dir = str(work_dir)
        report.report_dir = str(report_dir)
        report.config_path = str(generated_config_path)
        report.site_dir = str(site_dir)
        report.containers = [
            {
                "key": container.key,
                "vmid": container.vmid,
                "hostname": container.hostname,
                "guest_machine": f"{ORB_NSPAWN_NAME_PREFIX}-{container.vmid}",
            }
        ]
        report.capabilities = {
            "real-prestart-hook": True,
            "backend-stage-apply": True,
            "nested-nixos-guest": True,
            "proxmoxlxc-enabled": True,
            "local-network-override": True,
            "compiled-secret-stores": True,
            "relay-identities": True,
            "podman-secret-driver": True,
        }

        script_text = build_remote_probe_script(
            repo_root=repo_root,
            relay_tree=host_tree,
            pve_conf_path=pve_conf_path,
            bootstrap_config_path=bootstrap_config_path,
            container=container,
            timeout_seconds=args.timeout_seconds,
        )
        result = run_orb_probe(report, artifacts_dir, machine=args.machine, script_text=script_text)

        report.add_assertion(
            container.key,
            "managed config applied",
            bool(result.get("hashes_match")),
            f"current={result.get('current_hash')!r} applied={result.get('applied_hash')!r}",
        )
        status_file = result.get("status_file")
        if not isinstance(status_file, dict):
            raise ProxnixWorkstationError("orb probe result is missing status_file")
        observed = status_file.get("observed")
        if not isinstance(observed, dict):
            raise ProxnixWorkstationError("orb probe result is missing status_file.observed")
        for name, observed_key, expected in build_expected_assertions(token):
            actual = observed.get(observed_key)
            report.add_assertion(
                container.key,
                name,
                actual == expected,
                f"actual={actual!r} expected={expected!r}",
            )
        report.add_assertion(
            container.key,
            "status page served matching JSON",
            bool(result.get("status_http_matches_file")),
            f"http_status={result.get('status_http', {}).get('status')!r} file_status={status_file.get('status')!r}",
        )

        if report.status == "running":
            report.status = "passed"
        report.finished_at = utc_now()
        render_report_files(report, report_dir)
        print(f"Orb probe complete. Report: {report_dir / 'report.md'}")
        return 0 if report.status == "passed" else 1
    except (ConfigError, ProxnixWorkstationError, CommandError) as exc:
        report.add_error(str(exc))
        report.finished_at = utc_now()
        render_report_files(report, Path(report.report_dir) if report.report_dir else report_dir)
        print(f"error: {exc}")
        if report.report_dir:
            print(f"Partial report: {Path(report.report_dir) / 'report.md'}")
        return 1
