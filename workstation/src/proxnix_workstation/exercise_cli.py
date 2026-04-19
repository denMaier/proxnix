from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
import textwrap
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Sequence

from .config import WorkstationConfig, load_workstation_config
from .errors import ConfigError, ProxnixWorkstationError
from .paths import SitePaths
from .runtime import CommandError, run_command, shell_join
from .secrets_cli import (
    container_recipients,
    ensure_container_identity,
    ensure_host_relay_identity,
    group_recipients,
    shared_recipients,
    sops_set_local,
)
from .sops_ops import ensure_private_permissions
from .ssh_ops import SSHSession


DEFAULT_WORK_DIR = Path(".codex-staging/lxc-exercise")
DEFAULT_BASE_VMID = 940
DEFAULT_TIMEOUT_SECONDS = 3600
DEFAULT_SETTLE_SECONDS = 60
NIXOS_BASH = "/run/current-system/sw/bin/bash"
NIXOS_SYSTEMCTL = "/run/current-system/sw/bin/systemctl"
NIXOS_JOURNALCTL = "/run/current-system/sw/bin/journalctl"
STATUS_PORT = 18080


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
    token: str = ""
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
            "token": self.token,
            "containers": self.containers,
            "capabilities": self.capabilities,
            "summary": self.summary(),
            "assertions": [asdict(item) for item in self.assertions],
            "commands": [asdict(item) for item in self.commands],
            "errors": list(self.errors),
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_containers(base_vmid: int) -> list[ExerciseContainer]:
    return [
        ExerciseContainer(
            key="baseline",
            vmid=str(base_vmid),
            hostname="proxnix-exercise-baseline",
            memory_mb=3072,
            swap_mb=1024,
            cores=2,
            disk_gb=12,
            secret_groups=("exercise-group", "podman-group"),
        ),
    ]


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
    args: Sequence[str],
    *,
    check: bool = True,
) -> CompletedProcess[str]:
    completed = run_command(args, check=False, capture_output=True)
    command = shell_join(args)
    record_command(report, artifacts_dir, label, command, completed)
    if check:
        raise_for_completed(command, completed)
    return completed


def run_logged_remote_command(
    report: RunReport,
    artifacts_dir: Path,
    session: SSHSession,
    label: str,
    remote_command: str,
    *,
    check: bool = True,
) -> CompletedProcess[str]:
    completed = session.run(remote_command, check=False, capture_output=True)
    command = f"ssh {shlex.quote(session.host)} {remote_command}"
    record_command(report, artifacts_dir, label, command, completed)
    if check:
        raise_for_completed(command, completed)
    return completed


def parse_pct_config_hostname(config_text: str) -> str | None:
    for raw_line in config_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("hostname:"):
            continue
        _, _, hostname = line.partition(":")
        hostname = hostname.strip()
        return hostname or None
    return None


def existing_container_hostname(
    report: RunReport,
    artifacts_dir: Path,
    session: SSHSession,
    vmid: str,
) -> str | None:
    quoted_conf = shlex.quote(f"/etc/pve/lxc/{vmid}.conf")
    quoted_vmid = shlex.quote(vmid)
    remote_command = textwrap.dedent(
        f"""
        if [ -f {quoted_conf} ]; then
          pct config {quoted_vmid}
        fi
        """
    ).strip()
    completed = run_logged_remote_command(
        report,
        artifacts_dir,
        session,
        f"inspect-existing-{vmid}",
        remote_command,
        check=False,
    )
    if completed.returncode != 0:
        raise_for_completed(f"pct config {vmid}", completed)
    if not completed.stdout.strip():
        return None
    hostname = parse_pct_config_hostname(completed.stdout)
    return "" if hostname is None else hostname


def build_existing_container_cleanup_command(vmid: str) -> str:
    quoted_vmid = shlex.quote(vmid)
    quoted_conf = shlex.quote(f"/etc/pve/lxc/{vmid}.conf")
    quoted_config_dir = shlex.quote(f"/var/lib/proxnix/containers/{vmid}")
    quoted_private_dir = shlex.quote(f"/var/lib/proxnix/private/containers/{vmid}")
    return textwrap.dedent(
        f"""
        status="$(pct status {quoted_vmid} 2>/dev/null || true)"
        if [ "$status" = "status: running" ]; then
          pct stop {quoted_vmid}
        fi
        if [ -f {quoted_conf} ]; then
          pct unmount {quoted_vmid} >/dev/null 2>&1 || true
          pct unlock {quoted_vmid} >/dev/null 2>&1 || true
          pct destroy {quoted_vmid}
        fi
        rm -rf {quoted_config_dir} {quoted_private_dir}
        """
    ).strip()


def ensure_container_slots_available(
    report: RunReport,
    artifacts_dir: Path,
    session: SSHSession,
    containers: Sequence[ExerciseContainer],
    *,
    cleanup_existing: bool,
) -> None:
    for item in containers:
        hostname = existing_container_hostname(report, artifacts_dir, session, item.vmid)
        if hostname is None:
            continue
        if hostname != item.hostname:
            raise ProxnixWorkstationError(
                f"VMID {item.vmid} already exists on {session.host} with hostname "
                f"{hostname!r}; refusing to destroy a non-exercise container"
            )
        if not cleanup_existing:
            raise ProxnixWorkstationError(
                f"VMID {item.vmid} already exists on {session.host} from an earlier "
                f"exercise run; rerun with --cleanup-existing or choose a different --base-vmid"
            )
        run_logged_remote_command(
            report,
            artifacts_dir,
            session,
            f"cleanup-existing-{item.vmid}",
            build_existing_container_cleanup_command(item.vmid),
        )


def build_generated_config(
    source: WorkstationConfig,
    *,
    config_path: Path,
    site_dir: Path,
    host: str,
) -> WorkstationConfig:
    return WorkstationConfig(
        config_file=config_path,
        site_dir=site_dir,
        master_identity=source.master_identity,
        hosts=(host,),
        ssh_identity=source.ssh_identity,
        remote_dir=source.remote_dir,
        remote_priv_dir=source.remote_priv_dir,
        remote_host_relay_identity=source.remote_host_relay_identity,
        scripts_dir=source.scripts_dir,
    )


def render_config_file(config: WorkstationConfig) -> str:
    lines = [
        f"PROXNIX_SITE_DIR={shlex.quote(str(config.site_dir))}",
        f"PROXNIX_MASTER_IDENTITY={shlex.quote(str(config.master_identity))}",
        f"PROXNIX_HOSTS={shlex.quote(' '.join(config.hosts))}",
        f"PROXNIX_REMOTE_DIR={shlex.quote(str(config.remote_dir))}",
        f"PROXNIX_REMOTE_PRIV_DIR={shlex.quote(str(config.remote_priv_dir))}",
        f"PROXNIX_REMOTE_HOST_RELAY_IDENTITY={shlex.quote(str(config.remote_host_relay_identity))}",
    ]
    if config.ssh_identity is not None:
        lines.append(f"PROXNIX_SSH_IDENTITY={shlex.quote(str(config.ssh_identity))}")
    if config.scripts_dir is not None:
        lines.append(f"PROXNIX_SCRIPTS_DIR={shlex.quote(str(config.scripts_dir))}")
    return "\n".join(lines) + "\n"


def replace_placeholder(text: str, token: str) -> str:
    return text.replace("__TOKEN__", token)


def replace_nixos_shell_placeholder(text: str) -> str:
    return text.replace("__NIXOS_SHELL__", NIXOS_BASH)


SITE_NIX = """\
{ pkgs, ... }: {
  proxnix.common.extraPackages = with pkgs; [
    curl
    jq
    podman
    python3
  ];
}
"""


TEMPLATE_DEFAULT_NIX = """\
{ pkgs, ... }: {
  environment.systemPackages = with pkgs; [
    bash
    coreutils
    curl
    jq
    podman
    python3
  ];

  environment.etc."proxnix-exercise/template-marker".text = "exercise-base-template\\n";

  services.nginx = {
    enable = true;
    virtualHosts."proxnix-exercise" = {
      listen = [
        {
          addr = "0.0.0.0";
          port = 18080;
        }
      ];
      root = "/var/lib/proxnix-exercise/www";
      locations."/".tryFiles = "$uri $uri/ /index.html";
    };
  };

  networking.firewall.allowedTCPPorts = [ 18080 ];

  systemd.tmpfiles.rules = [
    "d /var/lib/proxnix-exercise 0755 root root -"
    "d /run/proxnix-exercise 0755 root root -"
    "d /var/lib/proxnix-exercise/www 0755 root root -"
  ];
}
"""


BASELINE_NIX = """\
{ pkgs, ... }:

let
  readerScript = pkgs.writeShellScript "proxnix-exercise-service-reader" ''
    set -eu
    install -d -m 0755 /var/lib/proxnix-exercise
    cp /run/proxnix-exercise/service-secret.txt /var/lib/proxnix-exercise/service-secret-snapshot.txt
    cp /run/proxnix-exercise/service-template.txt /var/lib/proxnix-exercise/service-template-snapshot.txt
    printf 'service-reader-ready\\n' > /var/lib/proxnix-exercise/service-reader.txt
    exec ${pkgs.coreutils}/bin/sleep infinity
  '';
  podmanCheck = pkgs.writeShellScript "proxnix-exercise-podman-check" ''
    set -euo pipefail
    install -d -m 0755 /var/lib/proxnix-exercise
    ${pkgs.podman}/bin/podman pull docker.io/library/alpine:3.20
    ${pkgs.podman}/bin/podman run --rm --secret podman_secret,type=mount docker.io/library/alpine:3.20 sh -eu -c 'cat /run/secrets/podman_secret' > /var/lib/proxnix-exercise/podman-secret.txt
    ${pkgs.podman}/bin/podman secret ls > /var/lib/proxnix-exercise/podman-secret-ls.txt
    ${pkgs.podman}/bin/podman info > /var/lib/proxnix-exercise/podman-info.txt
  '';
  statusWriter = pkgs.writeShellScript "proxnix-exercise-baseline-status" ''
    set -eu
    install -d -m 0755 /var/lib/proxnix-exercise /var/lib/proxnix-exercise/www
    ${pkgs.python3}/bin/python3 - <<'PY'
import html
import json
import pathlib
import subprocess

STATUS_DIR = pathlib.Path("/var/lib/proxnix-exercise/www")


def read_text(path: str) -> str | None:
    try:
        return pathlib.Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def run_capture(*args: str) -> tuple[int, str]:
    completed = subprocess.run(args, check=False, capture_output=True, text=True)
    return completed.returncode, completed.stdout.strip()


def make_check(name: str, actual, expected) -> dict[str, object]:
    return {
        "name": name,
        "ok": actual == expected,
        "actual": actual,
        "expected": expected,
    }


service_load_rc, service_load_state = run_capture(
    "/run/current-system/sw/bin/systemctl",
    "show",
    "-p",
    "LoadState",
    "--value",
    "proxnix-baseline-attached.service",
)
script_rc, script_output = run_capture("/var/lib/proxnix/runtime/bin/proxnix-baseline-report.sh")
shared_rc, shared_secret = run_capture("/var/lib/proxnix/runtime/bin/proxnix-secrets", "get", "shared_secret")
group_rc, group_secret = run_capture("/var/lib/proxnix/runtime/bin/proxnix-secrets", "get", "group_secret")
container_rc, container_secret = run_capture("/var/lib/proxnix/runtime/bin/proxnix-secrets", "get", "container_secret")
override_rc, override_secret = run_capture("/var/lib/proxnix/runtime/bin/proxnix-secrets", "get", "override_secret")

observed = {
    "template_marker": read_text("/etc/proxnix-exercise/template-marker"),
    "baseline_service_load_returncode": service_load_rc,
    "baseline_service_load_state": service_load_state,
    "attached_script_returncode": script_rc,
    "attached_script_output": script_output,
    "shared_secret_returncode": shared_rc,
    "shared_secret": shared_secret,
    "group_secret_returncode": group_rc,
    "group_secret": group_secret,
    "container_secret_returncode": container_rc,
    "container_secret": container_secret,
    "override_secret_returncode": override_rc,
    "override_secret": override_secret,
    "oneshot_secret": read_text("/var/lib/proxnix-exercise/oneshot-secret.txt"),
    "activation_template": read_text("/var/lib/proxnix-exercise/baseline-report.txt"),
    "service_reader_marker": read_text("/var/lib/proxnix-exercise/service-reader.txt"),
    "service_secret_snapshot": read_text("/var/lib/proxnix-exercise/service-secret-snapshot.txt"),
    "service_template_snapshot": read_text("/var/lib/proxnix-exercise/service-template-snapshot.txt"),
    "create_only_template": read_text("/var/lib/proxnix-exercise/create-only.txt"),
    "podman_secret": read_text("/var/lib/proxnix-exercise/podman-secret.txt"),
    "podman_secret_list_contains_secret": False,
    "podman_info_present": pathlib.Path("/var/lib/proxnix-exercise/podman-info.txt").is_file()
    and pathlib.Path("/var/lib/proxnix-exercise/podman-info.txt").stat().st_size > 0,
    "current_config_hash": read_text("/var/lib/proxnix/runtime/current-config-hash"),
    "applied_config_hash": read_text("/var/lib/proxnix/runtime/applied-config-hash"),
}
podman_secret_ls = read_text("/var/lib/proxnix-exercise/podman-secret-ls.txt")
if podman_secret_ls is not None:
    observed["podman_secret_list_contains_secret"] = "podman_secret" in podman_secret_ls

checks = [
    make_check("template marker present", observed["template_marker"], "exercise-base-template"),
    make_check("baseline service loaded", observed["baseline_service_load_state"], "loaded"),
    make_check("attached script executable", observed["attached_script_output"], "baseline-script-ok"),
    make_check("shared secret visible", observed["shared_secret"], "shared-__TOKEN__"),
    make_check("group secret visible", observed["group_secret"], "group-__TOKEN__"),
    make_check("container secret visible", observed["container_secret"], "container-__TOKEN__"),
    make_check("container overrides shared", observed["override_secret"], "container-override-__TOKEN__"),
    make_check("oneshot materialized", observed["oneshot_secret"], "oneshot-__TOKEN__"),
    make_check(
        "activation template rendered",
        observed["activation_template"],
        "shared=shared-__TOKEN__\\n"
        "group=group-__TOKEN__\\n"
        "container=container-__TOKEN__\\n"
        "override=container-override-__TOKEN__",
    ),
    make_check("service reader marker written", observed["service_reader_marker"], "service-reader-ready"),
    make_check("service secret snapshot", observed["service_secret_snapshot"], "service-__TOKEN__"),
    make_check("service template snapshot", observed["service_template_snapshot"], "service=service-__TOKEN__"),
    make_check("createOnly template rendered", observed["create_only_template"], "create-only=create-only-__TOKEN__"),
    make_check(
        "podman secret materialized in container workload",
        observed["podman_secret"],
        "podman-__TOKEN__",
    ),
    make_check("podman secret driver lists secret", observed["podman_secret_list_contains_secret"], True),
    make_check("podman info captured", observed["podman_info_present"], True),
    make_check(
        "managed config applied",
        observed["current_config_hash"],
        observed["applied_config_hash"],
    ),
]

data = {
    "container": "baseline",
    "hostname": "proxnix-exercise-baseline",
    "status": "passed" if all(item["ok"] for item in checks) else "failed",
    "checks": checks,
    "observed": observed,
}

STATUS_DIR.mkdir(parents=True, exist_ok=True)
(STATUS_DIR / "status.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\\n", encoding="utf-8")

rows = "\\n".join(
    "<tr><td>{name}</td><td>{status}</td><td><code>{actual}</code></td><td><code>{expected}</code></td></tr>".format(
        name=html.escape(str(item["name"])),
        status="PASS" if item["ok"] else "FAIL",
        actual=html.escape(json.dumps(item["actual"], ensure_ascii=True)),
        expected=html.escape(json.dumps(item["expected"], ensure_ascii=True)),
    )
    for item in checks
)
(STATUS_DIR / "index.html").write_text(
    (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>proxnix baseline status</title>"
        "<style>body{{font-family:monospace;margin:2rem;}}table{{border-collapse:collapse;}}td,th{{border:1px solid #ccc;padding:0.4rem;vertical-align:top;}}code{{white-space:pre-wrap;}}</style>"
        "</head><body><h1>Baseline status</h1><p>Overall: <strong>{status}</strong></p>"
        "<table><thead><tr><th>Check</th><th>Status</th><th>Actual</th><th>Expected</th></tr></thead><tbody>{rows}</tbody></table>"
        "<h2>Observed</h2><pre>{observed}</pre></body></html>"
    ).format(
        status=html.escape(data["status"]),
        rows=rows,
        observed=html.escape(json.dumps(observed, indent=2, sort_keys=True)),
    ),
    encoding="utf-8",
)
PY
  '';
in {
  imports = [ ../_template/exercise-base ];

  proxnix.secrets.files = {
    shared-secret = {
      secret = "shared_secret";
      path = "/var/lib/proxnix-exercise/shared-secret.txt";
      owner = "root";
      group = "root";
      mode = "0400";
    };
    group-secret = {
      secret = "group_secret";
      path = "/var/lib/proxnix-exercise/group-secret.txt";
      owner = "root";
      group = "root";
      mode = "0400";
    };
    container-secret = {
      secret = "container_secret";
      path = "/var/lib/proxnix-exercise/container-secret.txt";
      owner = "root";
      group = "root";
      mode = "0400";
    };
    override-secret = {
      secret = "override_secret";
      path = "/var/lib/proxnix-exercise/override-secret.txt";
      owner = "root";
      group = "root";
      mode = "0400";
    };
    service-secret = {
      secret = "service_secret";
      lifecycle = "service";
      service = "proxnix-exercise-service-reader.service";
      path = "/run/proxnix-exercise/service-secret.txt";
      owner = "root";
      group = "root";
      mode = "0400";
    };
  };

  proxnix.secrets.templates.baseline-report = {
    source = pkgs.writeText "baseline-report.txt" ''
      shared=__SHARED__
      group=__GROUP__
      container=__CONTAINER__
      override=__OVERRIDE__
    '';
    destination = "/var/lib/proxnix-exercise/baseline-report.txt";
    owner = "root";
    group = "root";
    mode = "0600";
    substitutions = {
      "__SHARED__" = { secret = "shared_secret"; };
      "__GROUP__" = { secret = "group_secret"; };
      "__CONTAINER__" = { secret = "container_secret"; };
      "__OVERRIDE__" = { secret = "override_secret"; };
    };
  };

  proxnix.secrets.templates.service-template = {
    lifecycle = "service";
    service = "proxnix-exercise-service-reader.service";
    source = pkgs.writeText "service-template.txt" ''
      service=__SERVICE__
    '';
    destination = "/run/proxnix-exercise/service-template.txt";
    owner = "root";
    group = "root";
    mode = "0400";
    substitutions = {
      "__SERVICE__" = { secret = "service_secret"; };
    };
  };

  proxnix.secrets.templates.create-only = {
    source = pkgs.writeText "create-only.txt" ''
      create-only=__CREATE_ONLY__
    '';
    destination = "/var/lib/proxnix-exercise/create-only.txt";
    owner = "root";
    group = "root";
    mode = "0600";
    createOnly = true;
    wantedBy = [ "multi-user.target" ];
    substitutions = {
      "__CREATE_ONLY__" = { secret = "create_only_secret"; };
    };
  };

  proxnix.secrets.oneshot.baseline-oneshot = {
    secret = "oneshot_secret";
    wantedBy = [ "multi-user.target" ];
    runtimeInputs = [ pkgs.coreutils ];
    script = ''
      install -d -m 0755 /var/lib/proxnix-exercise
      install -m 0600 "$PROXNIX_SECRET_FILE" /var/lib/proxnix-exercise/oneshot-secret.txt
    '';
  };

  systemd.services.proxnix-exercise-service-reader = {
    description = "Read service-lifetime proxnix exercise secrets";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type = "simple";
      ExecStart = readerScript;
      Restart = "always";
      RestartSec = "2s";
    };
  };

  virtualisation.podman.enable = true;

  systemd.services.proxnix-exercise-podman = {
    description = "Run proxnix podman secret driver exercise";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = podmanCheck;
      Restart = "on-failure";
      RestartSec = "10s";
    };
  };

  systemd.services.proxnix-exercise-baseline-status = {
    description = "Publish proxnix baseline exercise status page";
    wantedBy = [ "multi-user.target" ];
    after = [ "nginx.service" "proxnix-exercise-service-reader.service" "proxnix-exercise-podman.service" ];
    wants = [ "nginx.service" "proxnix-exercise-service-reader.service" "proxnix-exercise-podman.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = statusWriter;
    };
  };

  systemd.services.proxnix-baseline-attached = {
    description = "Baseline exercise attached-equivalent unit";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.coreutils}/bin/true";
      RemainAfterExit = true;
    };
  };
}
"""


BASELINE_SCRIPT = """\
#!__NIXOS_SHELL__
set -eu
printf 'baseline-script-ok\\n'
"""


def render_site_fixture(site_dir: Path, containers: Sequence[ExerciseContainer], token: str) -> None:
    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)

    write_text(site_dir / "site.nix", SITE_NIX)
    write_text(site_dir / "containers" / "_template" / "exercise-base" / "default.nix", TEMPLATE_DEFAULT_NIX)

    baseline = next(item for item in containers if item.key == "baseline")

    for item in containers:
        write_text(site_dir / "containers" / item.vmid / "templates" / "exercise-base.template", "")
        if item.secret_groups:
            write_text(
                site_dir / "containers" / item.vmid / "secret-groups.list",
                "\n".join(item.secret_groups) + "\n",
            )

    write_text(
        site_dir / "containers" / baseline.vmid / "dropins" / "exercise.nix",
        replace_placeholder(BASELINE_NIX, token),
    )
    write_text(
        site_dir / "containers" / baseline.vmid / "dropins" / "proxnix-baseline-report.sh",
        replace_nixos_shell_placeholder(BASELINE_SCRIPT),
        mode=0o755,
    )


def seed_site_secrets(config: WorkstationConfig, containers: Sequence[ExerciseContainer], token: str) -> None:
    site_paths = SitePaths.from_config(config)
    ensure_host_relay_identity(config, site_paths)
    ensure_private_permissions(site_paths.private_dir)

    shared_values = {
        "shared_secret": f"shared-{token}",
        "override_secret": f"shared-override-{token}",
        "oneshot_secret": f"oneshot-{token}",
    }
    shared_recips = shared_recipients(config, site_paths)
    for name, value in shared_values.items():
        sops_set_local(config, site_paths, site_paths.shared_store, shared_recips, name, value)

    group_values = {
        "exercise-group": {
            "group_secret": f"group-{token}",
        },
        "podman-group": {
            "podman_secret": f"podman-{token}",
        },
    }
    group_recips = group_recipients(config, site_paths)
    for group, values in group_values.items():
        for name, value in values.items():
            sops_set_local(config, site_paths, site_paths.group_store(group), group_recips, name, value)

    for item in containers:
        ensure_container_identity(config, site_paths, item.vmid)

    baseline = next(item for item in containers if item.key == "baseline")
    baseline_recips = container_recipients(config, site_paths, baseline.vmid)
    sops_set_local(
        config,
        site_paths,
        site_paths.container_store(baseline.vmid),
        baseline_recips,
        "container_secret",
        f"container-{token}",
    )
    sops_set_local(
        config,
        site_paths,
        site_paths.container_store(baseline.vmid),
        baseline_recips,
        "override_secret",
        f"container-override-{token}",
    )
    sops_set_local(
        config,
        site_paths,
        site_paths.container_store(baseline.vmid),
        baseline_recips,
        "service_secret",
        f"service-{token}",
    )
    sops_set_local(
        config,
        site_paths,
        site_paths.container_store(baseline.vmid),
        baseline_recips,
        "create_only_secret",
        f"create-only-{token}",
    )


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


def upload_public_key(
    report: RunReport,
    artifacts_dir: Path,
    session: SSHSession,
    public_key: str,
) -> str:
    remote_tmp = run_logged_remote_command(
        report,
        artifacts_dir,
        session,
        "remote-mktemp-pubkey",
        "mktemp /tmp/proxnix-exercise-pubkey.XXXXXX",
    ).stdout.strip()
    local_tmp = artifacts_dir / "exercise.pub"
    local_tmp.write_text(public_key + "\n", encoding="utf-8")
    command = [
        "rsync",
        "-a",
        "-e",
        session.rsync_ssh_command(),
        str(local_tmp),
        f"{session.host}:{remote_tmp}",
    ]
    run_logged_local_command(report, artifacts_dir, "upload-pubkey", command)
    return remote_tmp


def pct_exec_shell(session: SSHSession, vmid: str, script: str, *, check: bool = True) -> CompletedProcess[str]:
    remote = (
        f"pct exec {shlex.quote(vmid)} -- {shlex.quote(NIXOS_BASH)} "
        f"-lc {shlex.quote(script)}"
    )
    completed = session.run(remote, check=False, capture_output=True)
    if check:
        raise_for_completed(remote, completed)
    return completed


def wait_for_apply(session: SSHSession, vmid: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        completed = pct_exec_shell(
            session,
            vmid,
            textwrap.dedent(
                f"""
                current=""
                applied=""
                if [ -r /var/lib/proxnix/runtime/current-config-hash ]; then
                  IFS= read -r current < /var/lib/proxnix/runtime/current-config-hash || true
                fi
                if [ -r /var/lib/proxnix/runtime/applied-config-hash ]; then
                  IFS= read -r applied < /var/lib/proxnix/runtime/applied-config-hash || true
                fi
                if [ -n "$current" ] && [ "$current" = "$applied" ]; then
                  printf 'ready\\n'
                  exit 0
                fi
                if [ -x {shlex.quote(NIXOS_SYSTEMCTL)} ] && {shlex.quote(NIXOS_SYSTEMCTL)} is-failed --quiet proxnix-apply-config.service; then
                  printf 'failed\\n'
                  if [ -x {shlex.quote(NIXOS_JOURNALCTL)} ]; then
                    {shlex.quote(NIXOS_JOURNALCTL)} -u proxnix-apply-config.service -b --no-pager -n 80 >&2 || true
                  fi
                  exit 2
                fi
                printf 'waiting\\n'
                exit 1
                """
            ).strip(),
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip() == "ready":
            return
        if completed.returncode == 2 and completed.stdout.strip() == "failed":
            raise_for_completed(f"pct exec {vmid} -- wait-for-apply", completed)
        if (
            completed.returncode == 2
            and "Configuration file 'nodes/" in completed.stderr
            and f"/lxc/{vmid}.conf' does not exist" in completed.stderr
        ):
            time.sleep(10)
            continue
        time.sleep(10)
    raise ProxnixWorkstationError(f"timed out waiting for proxnix apply in container {vmid}")


def assert_equal(report: RunReport, scope: str, name: str, actual: str, expected: str) -> None:
    report.add_assertion(scope, name, actual == expected, f"expected={expected!r} actual={actual!r}")


def assert_true(report: RunReport, scope: str, name: str, condition: bool, detail: str) -> None:
    report.add_assertion(scope, name, condition, detail)


def guest_debug_units(item: ExerciseContainer) -> tuple[str, ...]:
    if item.key == "baseline":
        return (
            "proxnix-apply-config.service",
            "proxnix-baseline-attached.service",
            "proxnix-secret-oneshot-proxnix-common-admin-password.service",
            "proxnix-secret-template-baseline-report.service",
            "proxnix-secret-oneshot-baseline-oneshot.service",
            "proxnix-exercise-service-reader.service",
            "proxnix-exercise-podman.service",
            "nginx.service",
            "proxnix-exercise-baseline-status.service",
        )
    raise ProxnixWorkstationError(f"unsupported exercise container key: {item.key}")


def guest_assertion_start_units(item: ExerciseContainer) -> tuple[str, ...]:
    if item.key == "baseline":
        return (
            "nginx.service",
            "proxnix-exercise-service-reader.service",
            "proxnix-exercise-podman.service",
            "proxnix-exercise-baseline-status.service",
        )
    raise ProxnixWorkstationError(f"unsupported exercise container key: {item.key}")


def start_guest_assertion_services(
    report: RunReport,
    artifacts_dir: Path,
    session: SSHSession,
    item: ExerciseContainer,
) -> None:
    units = guest_assertion_start_units(item)
    script = f"{shlex.quote(NIXOS_SYSTEMCTL)} start {' '.join(shlex.quote(unit) for unit in units)}"
    completed = run_logged_remote_command(
        report,
        artifacts_dir,
        session,
        f"start-guest-assertion-services-{item.key}",
        (
            f"pct exec {shlex.quote(item.vmid)} -- {shlex.quote(NIXOS_BASH)} "
            f"-lc {shlex.quote(script)}"
        ),
        check=False,
    )
    report.add_assertion(
        item.key,
        "exercise services started",
        completed.returncode == 0,
        f"returncode={completed.returncode}",
    )


def collect_guest_debug_snapshot(
    report: RunReport,
    artifacts_dir: Path,
    session: SSHSession,
    item: ExerciseContainer,
) -> None:
    units = guest_debug_units(item)
    unit_args = " ".join(shlex.quote(unit) for unit in units)
    script = textwrap.dedent(
        f"""
        set -eu
        current=""
        applied=""
        if [ -r /var/lib/proxnix/runtime/current-config-hash ]; then
          IFS= read -r current < /var/lib/proxnix/runtime/current-config-hash || true
        fi
        if [ -r /var/lib/proxnix/runtime/applied-config-hash ]; then
          IFS= read -r applied < /var/lib/proxnix/runtime/applied-config-hash || true
        fi
        printf 'current=%s\\napplied=%s\\n' "$current" "$applied"
        echo "--- units ---"
        {shlex.quote(NIXOS_SYSTEMCTL)} list-units --all {unit_args} --no-pager || true
        echo "--- unit-files ---"
        {shlex.quote(NIXOS_SYSTEMCTL)} list-unit-files {unit_args} --no-pager || true
        echo "--- web-root ---"
        ls -la /var/lib/proxnix-exercise || true
        ls -la /var/lib/proxnix-exercise/www || true
        echo "--- journal ---"
        {shlex.quote(NIXOS_JOURNALCTL)} -b --no-pager -n 160 {' '.join(f"-u {shlex.quote(unit)}" for unit in units)} || true
        """
    ).strip()
    run_logged_remote_command(
        report,
        artifacts_dir,
        session,
        f"debug-status-{item.key}",
        f"pct exec {shlex.quote(item.vmid)} -- {shlex.quote(NIXOS_BASH)} -lc {shlex.quote(script)}",
        check=False,
    )


def discover_guest_ipv4(
    report: RunReport,
    artifacts_dir: Path,
    session: SSHSession,
    vmid: str,
) -> str:
    python_code = textwrap.dedent(
        """
        import json
        import subprocess

        completed = subprocess.run(
            ["/run/current-system/sw/bin/ip", "-j", "-4", "addr", "show", "up"],
            check=True,
            capture_output=True,
            text=True,
        )
        interfaces = json.loads(completed.stdout)
        for interface in interfaces:
            if interface.get("ifname") == "lo":
                continue
            for address in interface.get("addr_info", []):
                local = address.get("local")
                if local and not local.startswith("127."):
                    print(local)
                    raise SystemExit(0)
        raise SystemExit(1)
        """
    ).strip()
    completed = run_logged_remote_command(
        report,
        artifacts_dir,
        session,
        f"discover-ip-{vmid}",
        " ".join(
            [
                "pct exec",
                shlex.quote(vmid),
                "--",
                shlex.quote("/run/current-system/sw/bin/python3"),
                "-c",
                shlex.quote(python_code),
            ]
        ),
    )
    ip_address = completed.stdout.strip()
    if not ip_address:
        raise ProxnixWorkstationError(f"could not determine IPv4 address for container {vmid}")
    return ip_address


def fetch_guest_status_document(
    report: RunReport,
    artifacts_dir: Path,
    session: SSHSession,
    item: ExerciseContainer,
    ip_address: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    url = f"http://{ip_address}:{STATUS_PORT}/status.json"
    remote_command = f"curl -fsS {shlex.quote(url)}"
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        completed = session.run(remote_command, check=False, capture_output=True)
        if completed.returncode == 0:
            try:
                data = json.loads(completed.stdout)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                break
        time.sleep(5)
    else:
        collect_guest_debug_snapshot(report, artifacts_dir, session, item)
        failure = run_logged_remote_command(
            report,
            artifacts_dir,
            session,
            f"fetch-status-{item.key}",
            remote_command,
            check=False,
        )
        raise_for_completed(remote_command, failure)
        raise ProxnixWorkstationError(f"status endpoint for container {item.vmid} never returned JSON")

    recorded = run_logged_remote_command(
        report,
        artifacts_dir,
        session,
        f"fetch-status-{item.key}",
        remote_command,
    )
    try:
        return json.loads(recorded.stdout)
    except json.JSONDecodeError as exc:
        raise ProxnixWorkstationError(
            f"status endpoint for container {item.vmid} returned invalid JSON"
        ) from exc


def add_status_document_assertions(report: RunReport, scope: str, document: dict[str, Any]) -> None:
    status = document.get("status")
    assert_equal(report, scope, "status page overall", str(status), "passed")

    checks = document.get("checks")
    if not isinstance(checks, list):
        report.add_assertion(scope, "status page checks present", False, "status page omitted checks array")
        return

    for item in checks:
        if not isinstance(item, dict):
            report.add_assertion(scope, "status page check shape", False, f"invalid check payload: {item!r}")
            continue
        name = str(item.get("name", "unnamed check"))
        ok = bool(item.get("ok"))
        actual = item.get("actual")
        expected = item.get("expected")
        report.add_assertion(
            scope,
            name,
            ok,
            f"expected={expected!r} actual={actual!r}",
        )


def run_guest_assertions(
    report: RunReport,
    artifacts_dir: Path,
    session: SSHSession,
    containers: Sequence[ExerciseContainer],
    timeout_seconds: int,
) -> None:
    container_entries = {container["key"]: container for container in report.containers}

    for item in containers:
        ip_address = discover_guest_ipv4(report, artifacts_dir, session, item.vmid)
        status_url = f"http://{ip_address}:{STATUS_PORT}/"
        container_entries[item.key]["ip_address"] = ip_address
        container_entries[item.key]["status_url"] = status_url

        document = fetch_guest_status_document(
            report,
            artifacts_dir,
            session,
            item,
            ip_address,
            timeout_seconds,
        )
        add_status_document_assertions(report, item.key, document)


def render_markdown_report(report: RunReport) -> str:
    summary = report.summary()
    lines = [
        "# proxnix LXC exercise report",
        "",
        f"- Status: `{report.status}`",
        f"- Host: `{report.host}`",
        f"- Started: `{report.started_at}`",
        f"- Finished: `{report.finished_at}`",
        f"- Work dir: `{report.work_dir}`",
        f"- Site dir: `{report.site_dir}`",
        f"- Config: `{report.config_path}`",
        f"- Token: `{report.token}`",
        "",
        "## Capability coverage",
        "",
    ]
    for name, enabled in sorted(report.capabilities.items()):
        lines.append(f"- `{name}`: {'covered' if enabled else 'not covered'}")

    lines.extend(
        [
            "",
            "## Containers",
            "",
        ]
    )
    for container in report.containers:
        line = f"- `{container['key']}`: vmid `{container['vmid']}`, hostname `{container['hostname']}`"
        ip_address = container.get("ip_address")
        status_url = container.get("status_url")
        if ip_address:
            line += f", ip `{ip_address}`"
        if status_url:
            line += f", status `{status_url}`"
        lines.append(line)

    lines.extend(
        [
            "",
            "## Assertions",
            "",
            f"- Passed: `{summary['passed']}`",
            f"- Failed: `{summary['failed']}`",
            f"- Total: `{summary['total']}`",
            "",
        ]
    )
    for item in report.assertions:
        marker = "PASS" if item.status == "passed" else "FAIL"
        lines.append(f"- `{marker}` `{item.scope}` `{item.name}`: {item.detail}")

    if report.errors:
        lines.extend(
            [
                "",
                "## Errors",
                "",
            ]
        )
        for message in report.errors:
            lines.append(f"- {message}")

    lines.extend(
        [
            "",
            "## Command logs",
            "",
        ]
    )
    for command in report.commands:
        details = [f"rc={command.returncode}"]
        if command.stdout_path is not None:
            details.append(f"stdout={command.stdout_path}")
        if command.stderr_path is not None:
            details.append(f"stderr={command.stderr_path}")
        lines.append(f"- `{command.label}`: `{command.command}` ({', '.join(details)})")

    return "\n".join(lines) + "\n"


def choose_host(config: WorkstationConfig, override: str | None) -> str:
    if override is not None:
        return override
    if config.hosts:
        return config.hosts[0]
    raise ConfigError("no publish hosts configured; pass --host or set PROXNIX_HOSTS")


def render_report_files(report: RunReport, report_dir: Path) -> None:
    write_text(report_dir / "report.json", json.dumps(report.to_json_data(), indent=2, sort_keys=True) + "\n")
    write_text(report_dir / "report.md", render_markdown_report(report))


def run_python_module(
    report: RunReport,
    artifacts_dir: Path,
    label: str,
    module: str,
    args: Sequence[str],
    *,
    check: bool = True,
) -> CompletedProcess[str]:
    return run_logged_local_command(
        report,
        artifacts_dir,
        label,
        [sys.executable, "-m", module, *args],
        check=check,
    )


def create_containers(
    report: RunReport,
    artifacts_dir: Path,
    session: SSHSession,
    args: argparse.Namespace,
    containers: Sequence[ExerciseContainer],
    remote_pubkey_path: str,
) -> None:
    for item in containers:
        completed = run_logged_remote_command(
            report,
            artifacts_dir,
            session,
            f"check-vmid-{item.vmid}-free",
            f"test ! -e /etc/pve/lxc/{item.vmid}.conf",
            check=False,
        )
        if completed.returncode != 0:
            raise ProxnixWorkstationError(f"VMID already exists on {session.host}: {item.vmid}")

    for item in containers:
        create_args = [
            "proxnix-create-lxc",
            "--yes",
            "--no-start",
            "--vmid",
            item.vmid,
            "--hostname",
            item.hostname,
            "--disk",
            str(item.disk_gb),
            "--memory",
            str(item.memory_mb),
            "--swap",
            str(item.swap_mb),
            "--cores",
            str(item.cores),
            "--bridge",
            args.bridge,
            "--ip",
            args.ip,
            "--ssh-public-keys",
            remote_pubkey_path,
        ]
        if args.template:
            create_args.extend(["--template", args.template])
        if args.storage:
            create_args.extend(["--storage", args.storage])
        if args.gw:
            create_args.extend(["--gw", args.gw])
        run_logged_remote_command(
            report,
            artifacts_dir,
            session,
            f"create-{item.key}",
            shell_join(create_args),
        )
        if args.nameserver:
            run_logged_remote_command(
                report,
                artifacts_dir,
                session,
                f"set-nameserver-{item.key}",
                shell_join(
                    [
                        "pct",
                        "set",
                        item.vmid,
                        "--nameserver",
                        args.nameserver,
                    ]
                ),
            )

    for item in containers:
        run_logged_remote_command(
            report,
            artifacts_dir,
            session,
            f"start-{item.key}",
            f"pct start {shlex.quote(item.vmid)}",
        )


def build_parser(*, prog: str = "proxnix-lxc-exercise") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--config", type=Path, help="Path to the proxnix workstation config file")
    parser.add_argument("--host", help="Target Proxmox host override")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--base-vmid", type=int, default=DEFAULT_BASE_VMID)
    parser.add_argument("--template", help="Optional explicit NixOS template volid")
    parser.add_argument("--storage", help="Optional explicit rootfs storage")
    parser.add_argument("--bridge", default="vmbr0")
    parser.add_argument("--ip", default="dhcp")
    parser.add_argument("--gw", help="Gateway for static --ip values")
    parser.add_argument("--nameserver", help="Optional PVE nameserver to inject before first boot")
    parser.add_argument(
        "--cleanup-existing",
        action="store_true",
        help="Destroy stale exercise containers already present at the selected VMIDs",
    )
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--settle-seconds",
        type=int,
        default=DEFAULT_SETTLE_SECONDS,
        help="Extra time to wait after proxnix apply completes before asserting guest state",
    )
    return parser


def main(argv: list[str] | None = None, *, prog: str = "proxnix-lxc-exercise") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)

    started_at = utc_now()
    report = RunReport(started_at=started_at)
    work_dir = args.work_dir.expanduser().resolve()
    report_dir = work_dir / "reports" / "latest"

    try:
        source_config = load_workstation_config(args.config)
        host = choose_host(source_config, args.host)
        site_dir = work_dir / "site"
        artifacts_dir = report_dir / "artifacts"
        config_path = work_dir / "config"
        token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        containers = build_containers(args.base_vmid)

        if report_dir.exists():
            shutil.rmtree(report_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        generated_config = build_generated_config(
            source_config,
            config_path=config_path,
            site_dir=site_dir,
            host=host,
        )
        write_text(config_path, render_config_file(generated_config), mode=0o600)
        render_site_fixture(site_dir, containers, token)
        seed_site_secrets(generated_config, containers, token)

        report.host = host
        report.work_dir = str(work_dir)
        report.report_dir = str(report_dir)
        report.config_path = str(config_path)
        report.site_dir = str(site_dir)
        report.token = token
        report.containers = [
            {
                "key": item.key,
                "vmid": item.vmid,
                "hostname": item.hostname,
                "memory_mb": item.memory_mb,
                "swap_mb": item.swap_mb,
                "cores": item.cores,
                "disk_gb": item.disk_gb,
                "secret_groups": list(item.secret_groups),
            }
            for item in containers
        ]
        report.capabilities = {
            "template-imports": True,
            "dropin-nix-modules": True,
            "attached-shell-scripts": True,
            "nix-managed-systemd-units": True,
            "shared-secrets": True,
            "group-secrets": True,
            "container-secrets": True,
            "activation-secret-files": True,
            "activation-secret-templates": True,
            "service-lifetime-secret-files": True,
            "service-lifetime-secret-templates": True,
            "createOnly-secret-templates": True,
            "oneshot-secret-consumers": True,
            "podman-secret-driver": True,
            "publish-reporting": True,
            "site-doctor": True,
            "host-doctor": True,
            "vmid-scoped-config-only-dry-run": True,
            "proxnix-create-lxc": True,
        }

        with SSHSession(generated_config, host) as session:
            ensure_container_slots_available(
                report,
                artifacts_dir,
                session,
                containers,
                cleanup_existing=args.cleanup_existing,
            )

        run_python_module(
            report,
            artifacts_dir,
            "doctor-site-only",
            "proxnix_workstation.doctor_cli",
            ["--config", str(config_path), "--site-only"],
        )
        run_python_module(
            report,
            artifacts_dir,
            "publish-dry-run",
            "proxnix_workstation.publish_cli",
            ["--config", str(config_path), "--dry-run", "--report-changes", host],
        )
        run_python_module(
            report,
            artifacts_dir,
            "publish-apply",
            "proxnix_workstation.publish_cli",
            ["--config", str(config_path), "--report-changes", host],
        )

        public_identity = source_config.ssh_identity or source_config.master_identity
        public_key = derive_public_key(public_identity)

        with SSHSession(generated_config, host) as session:
            run_logged_remote_command(
                report,
                artifacts_dir,
                session,
                "host-doctor-preflight",
                "proxnix-doctor --host-only",
            )
            remote_pubkey_path = upload_public_key(report, artifacts_dir, session, public_key)
            try:
                create_containers(report, artifacts_dir, session, args, containers, remote_pubkey_path)
            finally:
                run_logged_remote_command(
                    report,
                    artifacts_dir,
                    session,
                    "cleanup-remote-pubkey",
                    f"rm -f {shlex.quote(remote_pubkey_path)}",
                    check=False,
                )

            for item in containers:
                wait_for_apply(session, item.vmid, args.timeout_seconds)
                report.add_assertion(item.key, "managed config applied", True, "current hash matches applied hash")
                start_guest_assertion_services(report, artifacts_dir, session, item)

            if args.settle_seconds > 0:
                time.sleep(args.settle_seconds)

            joined_vmids = " ".join(shlex.quote(item.vmid) for item in containers)
            run_logged_remote_command(
                report,
                artifacts_dir,
                session,
                "host-doctor-containers",
                f"proxnix-doctor {joined_vmids}",
            )

            run_guest_assertions(report, artifacts_dir, session, containers, args.timeout_seconds)

        run_python_module(
            report,
            artifacts_dir,
            "doctor-remote-drift",
            "proxnix_workstation.doctor_cli",
            ["--config", str(config_path), host],
        )
        run_python_module(
            report,
            artifacts_dir,
            "publish-vmid-config-only-dry-run",
            "proxnix_workstation.publish_cli",
            [
                "--config",
                str(config_path),
                "--dry-run",
                "--report-changes",
                "--config-only",
                "--vmid",
                containers[0].vmid,
                host,
            ],
        )

        if report.status == "running":
            report.status = "passed"
        report.finished_at = utc_now()
        render_report_files(report, report_dir)
        print(f"Exercise complete. Report: {report_dir / 'report.md'}")
        return 0 if report.status == "passed" else 1
    except (ConfigError, ProxnixWorkstationError, CommandError) as exc:
        report.add_error(str(exc))
        report.finished_at = utc_now()
        render_report_files(report, Path(report.report_dir) if report.report_dir else report_dir)
        print(f"error: {exc}")
        if report.report_dir:
            print(f"Partial report: {Path(report.report_dir) / 'report.md'}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
