from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import SitePaths

if TYPE_CHECKING:
    from .config import WorkstationConfig


STATUS_PORT = 18080

SITE_NIX = """\
{ pkgs, ... }: {
  proxnix.common.extraPackages = with pkgs; [
    curl
    jq
    podman
    python3
  ];

  environment.etc."proxnix-test-site/site-marker".text = "test-site-ok\\n";
}
"""


TEMPLATE_DEFAULT_NIX = f"""\
{{ pkgs, ... }}: {{
  environment.systemPackages = with pkgs; [
    bash
    coreutils
    curl
    jq
    podman
    python3
  ];

  environment.etc."proxnix-test-site/template-marker".text = "exercise-base-template\\n";

  services.nginx = {{
    enable = true;
    virtualHosts."proxnix-test-site" = {{
      listen = [
        {{
          addr = "0.0.0.0";
          port = {STATUS_PORT};
        }}
      ];
      root = "/var/lib/proxnix-test-site/www";
      locations."/".tryFiles = "$uri $uri/ /index.html";
    }};
  }};

  networking.firewall.allowedTCPPorts = [ {STATUS_PORT} ];

  systemd.tmpfiles.rules = [
    "d /var/lib/proxnix-test-site 0755 root root -"
    "d /run/proxnix-test-site 0755 root root -"
    "d /var/lib/proxnix-test-site/www 0755 root root -"
  ];
}}
"""


TEST_SITE_DROPIN_NIX = """\
{ lib, pkgs, ... }:

let
  serviceReader = pkgs.writeShellScript "proxnix-test-site-service-reader" ''
    set -eu
    install -d -m 0755 /var/lib/proxnix-test-site
    cp /run/proxnix-test-site/service-secret.txt /var/lib/proxnix-test-site/service-secret-snapshot.txt
    cp /run/proxnix-test-site/service-template.txt /var/lib/proxnix-test-site/service-template-snapshot.txt
    printf 'service-reader-ready\\n' > /var/lib/proxnix-test-site/service-reader.txt
    exec ${pkgs.coreutils}/bin/sleep infinity
  '';

  publicRuntimeReader = pkgs.writeShellScript "proxnix-test-site-public-runtime-reader" ''
    set -eu
    install -d -m 0755 /var/lib/proxnix-test-site
    printf '%s' "''${EXERCISE_PUBLIC_ENV_SECRET:-}" > /var/lib/proxnix-test-site/public-env.txt
    cat "''${CREDENTIALS_DIRECTORY}/exercise-public-credential" > /var/lib/proxnix-test-site/public-credential.txt
    printf 'public-runtime-ready\\n' > /var/lib/proxnix-test-site/public-runtime.txt
    exec ${pkgs.coreutils}/bin/sleep infinity
  '';

  podmanRoot = pkgs.buildEnv {
    name = "proxnix-test-site-podman-root";
    paths = [ pkgs.busybox ];
    pathsToLink = [ "/bin" ];
  };

  podmanImage = pkgs.dockerTools.buildImage {
    name = "proxnix-test-site/podman-secret";
    tag = "local";
    copyToRoot = podmanRoot;
    config = {
      Cmd = [ "/bin/sh" "-eu" "-c" "cat /run/secrets/podman_secret" ];
    };
  };

  podmanCheck = pkgs.writeShellScript "proxnix-test-site-podman-check" ''
    set -euo pipefail
    install -d -m 0755 /var/lib/proxnix-test-site
    ${pkgs.podman}/bin/podman load -i ${podmanImage} > /var/lib/proxnix-test-site/podman-load.stdout.txt 2> /var/lib/proxnix-test-site/podman-load.stderr.txt
    ${pkgs.podman}/bin/podman secret ls > /var/lib/proxnix-test-site/podman-secret-ls.txt 2> /var/lib/proxnix-test-site/podman-secret-ls.stderr.txt
    ${pkgs.podman}/bin/podman info > /var/lib/proxnix-test-site/podman-info.txt 2> /var/lib/proxnix-test-site/podman-info.stderr.txt
    ${pkgs.podman}/bin/podman run --rm --privileged --network=host --cgroups=disabled --security-opt seccomp=unconfined --secret podman_secret,type=mount localhost/proxnix-test-site/podman-secret:local > /var/lib/proxnix-test-site/podman-secret.txt 2> /var/lib/proxnix-test-site/podman-run.stderr.txt
  '';

  statusWriter = pkgs.writeShellScript "proxnix-test-site-status" ''
    set -eu
    install -d -m 0755 /var/lib/proxnix-test-site /var/lib/proxnix-test-site/www
    ${pkgs.python3}/bin/python3 - <<'PY'
import html
import json
import pathlib
import subprocess
import urllib.request

STATUS_DIR = pathlib.Path("/var/lib/proxnix-test-site/www")


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


def load_state(unit: str) -> str:
    _, value = run_capture("/run/current-system/sw/bin/systemctl", "show", "-p", "LoadState", "--value", unit)
    return value


def active_state(unit: str) -> str:
    _, value = run_capture("/run/current-system/sw/bin/systemctl", "show", "-p", "ActiveState", "--value", unit)
    return value


def result_state(unit: str) -> str:
    _, value = run_capture("/run/current-system/sw/bin/systemctl", "show", "-p", "Result", "--value", unit)
    return value


helper_sh_rc, helper_sh_output = run_capture("/var/lib/proxnix/runtime/bin/proxnix-test-site-report.sh")
helper_py_rc, helper_py_output = run_capture("/var/lib/proxnix/runtime/bin/proxnix-test-site-helper.py")
shared_rc, shared_secret = run_capture("/var/lib/proxnix/runtime/bin/proxnix-secrets", "get", "shared_secret")
group_rc, group_secret = run_capture("/var/lib/proxnix/runtime/bin/proxnix-secrets", "get", "group_secret")
container_rc, container_secret = run_capture("/var/lib/proxnix/runtime/bin/proxnix-secrets", "get", "container_secret")
override_rc, override_secret = run_capture("/var/lib/proxnix/runtime/bin/proxnix-secrets", "get", "override_secret")

observed = {
    "site_marker": read_text("/etc/proxnix-test-site/site-marker"),
    "probe_marker": read_text("/etc/proxnix-orb-probe"),
    "template_marker": read_text("/etc/proxnix-test-site/template-marker"),
    "attached_service_load_state": load_state("proxnix-test-site-attached.service"),
    "apply_service_load_state": load_state("proxnix-apply-config.service"),
    "public_runtime_secret_unit_load_state": load_state("proxnix-public-runtime-secret-proxnix-test-site-public-runtime.service"),
    "oneshot_unit_load_state": load_state("proxnix-secret-oneshot-test-site-oneshot.service"),
    "podman_service_load_state": load_state("proxnix-test-site-podman.service"),
    "podman_service_active_state": active_state("proxnix-test-site-podman.service"),
    "podman_service_result": result_state("proxnix-test-site-podman.service"),
    "helper_sh_returncode": helper_sh_rc,
    "helper_sh_output": helper_sh_output,
    "helper_py_returncode": helper_py_rc,
    "helper_py_output": helper_py_output,
    "shared_secret_returncode": shared_rc,
    "shared_secret": shared_secret,
    "group_secret_returncode": group_rc,
    "group_secret": group_secret,
    "container_secret_returncode": container_rc,
    "container_secret": container_secret,
    "override_secret_returncode": override_rc,
    "override_secret": override_secret,
    "public_shared_file": read_text("/var/lib/proxnix/secrets/shared-file"),
    "public_group_file": read_text("/var/lib/proxnix/secrets/group-file"),
    "public_container_file": read_text("/var/lib/proxnix/secrets/container-file"),
    "public_env_value": read_text("/var/lib/proxnix-test-site/public-env.txt"),
    "public_credential_value": read_text("/var/lib/proxnix-test-site/public-credential.txt"),
    "public_runtime_marker": read_text("/var/lib/proxnix-test-site/public-runtime.txt"),
    "public_rendered_config": read_text("/var/lib/proxnix-test-site/public-config.txt"),
    "public_create_only_config": read_text("/var/lib/proxnix-test-site/public-create-only.txt"),
    "oneshot_secret": read_text("/var/lib/proxnix-test-site/oneshot-secret.txt"),
    "activation_template": read_text("/var/lib/proxnix-test-site/internal-template.txt"),
    "service_reader_marker": read_text("/var/lib/proxnix-test-site/service-reader.txt"),
    "service_secret_snapshot": read_text("/var/lib/proxnix-test-site/service-secret-snapshot.txt"),
    "service_template_snapshot": read_text("/var/lib/proxnix-test-site/service-template-snapshot.txt"),
    "create_only_template": read_text("/var/lib/proxnix-test-site/create-only.txt"),
    "podman_secret": read_text("/var/lib/proxnix-test-site/podman-secret.txt"),
    "podman_load_stdout": read_text("/var/lib/proxnix-test-site/podman-load.stdout.txt"),
    "podman_load_stderr": read_text("/var/lib/proxnix-test-site/podman-load.stderr.txt"),
    "podman_run_stderr": read_text("/var/lib/proxnix-test-site/podman-run.stderr.txt"),
    "podman_secret_list_contains_secret": False,
    "podman_secret_list_output": read_text("/var/lib/proxnix-test-site/podman-secret-ls.txt"),
    "podman_secret_list_stderr": read_text("/var/lib/proxnix-test-site/podman-secret-ls.stderr.txt"),
    "podman_info_present": pathlib.Path("/var/lib/proxnix-test-site/podman-info.txt").is_file()
    and pathlib.Path("/var/lib/proxnix-test-site/podman-info.txt").stat().st_size > 0,
    "podman_info_stderr": read_text("/var/lib/proxnix-test-site/podman-info.stderr.txt"),
    "current_config_hash": read_text("/var/lib/proxnix/runtime/current-config-hash"),
    "applied_config_hash": read_text("/var/lib/proxnix/runtime/applied-config-hash"),
}
podman_secret_ls = observed["podman_secret_list_output"]
if podman_secret_ls is not None:
    observed["podman_secret_list_contains_secret"] = "podman_secret" in podman_secret_ls

checks = [
    make_check("template marker present", observed["template_marker"], "exercise-base-template"),
    make_check("site import rendered", observed["site_marker"], "test-site-ok"),
    make_check("orb probe dropin rendered", observed["probe_marker"], "orb-probe-ok"),
    make_check("attached service loaded", observed["attached_service_load_state"], "loaded"),
    make_check("apply service loaded", observed["apply_service_load_state"], "loaded"),
    make_check("public runtime secret unit loaded", observed["public_runtime_secret_unit_load_state"], "loaded"),
    make_check("internal oneshot unit loaded", observed["oneshot_unit_load_state"], "loaded"),
    make_check("podman exercise unit loaded", observed["podman_service_load_state"], "loaded"),
    make_check("attached shell helper executable", observed["helper_sh_output"], "helper-sh-ok"),
    make_check("attached python helper executable", observed["helper_py_output"], "helper-py-ok"),
    make_check("shared secret visible", observed["shared_secret"], "shared-__TOKEN__"),
    make_check("group secret visible", observed["group_secret"], "group-__TOKEN__"),
    make_check("container secret visible", observed["container_secret"], "container-__TOKEN__"),
    make_check("container overrides shared", observed["override_secret"], "container-override-__TOKEN__"),
    make_check("public shared file delivered", observed["public_shared_file"], "shared-__TOKEN__"),
    make_check("public group file delivered", observed["public_group_file"], "group-__TOKEN__"),
    make_check("public container file delivered", observed["public_container_file"], "container-__TOKEN__"),
    make_check("public env delivered", observed["public_env_value"], "env-__TOKEN__"),
    make_check("public credential delivered", observed["public_credential_value"], "credential-__TOKEN__"),
    make_check("public runtime service started", observed["public_runtime_marker"], "public-runtime-ready"),
    make_check(
        "public config rendered",
        observed["public_rendered_config"],
        "shared=shared-__TOKEN__\\n"
        "group=group-__TOKEN__\\n"
        "container=container-__TOKEN__\\n"
        "config=config-secret-__TOKEN__\\n"
        "literal=literal-__TOKEN__\\n"
        "enabled=true\\n"
        "number=7",
    ),
    make_check("public createOnly config rendered", observed["public_create_only_config"], "public-create-only=public-create-only-__TOKEN__"),
    make_check("internal oneshot materialized", observed["oneshot_secret"], "oneshot-__TOKEN__"),
    make_check(
        "internal activation template rendered",
        observed["activation_template"],
        "shared=shared-__TOKEN__\\n"
        "group=group-__TOKEN__\\n"
        "container=container-__TOKEN__\\n"
        "override=container-override-__TOKEN__",
    ),
    make_check("internal service reader marker written", observed["service_reader_marker"], "service-reader-ready"),
    make_check("internal service secret snapshot", observed["service_secret_snapshot"], "service-__TOKEN__"),
    make_check("internal service template snapshot", observed["service_template_snapshot"], "service=service-__TOKEN__"),
    make_check("internal createOnly template rendered", observed["create_only_template"], "create-only=create-only-__TOKEN__"),
    make_check("podman secret materialized in workload", observed["podman_secret"], "podman-__TOKEN__"),
    make_check("podman secret driver lists secret", observed["podman_secret_list_contains_secret"], True),
    make_check("podman info captured", observed["podman_info_present"], True),
    make_check("managed config applied", observed["current_config_hash"], observed["applied_config_hash"]),
]

data = {
    "container": "orb-probe",
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
        "<title>proxnix test-site status</title>"
        "<style>body{{font-family:monospace;margin:2rem;}}table{{border-collapse:collapse;}}td,th{{border:1px solid #ccc;padding:0.4rem;vertical-align:top;}}code{{white-space:pre-wrap;}}</style>"
        "</head><body><h1>proxnix test-site status</h1><p>Overall: <strong>{status}</strong></p>"
        "<table><thead><tr><th>Check</th><th>Status</th><th>Actual</th><th>Expected</th></tr></thead><tbody>{rows}</tbody></table>"
        "<h2>Observed</h2><pre>{observed}</pre></body></html>"
    ).format(
        status=html.escape(data["status"]),
        rows=rows,
        observed=html.escape(json.dumps(observed, indent=2, sort_keys=True)),
    ),
    encoding="utf-8",
)

try:
    http_payload = urllib.request.urlopen("http://127.0.0.1:18080/status.json", timeout=5).read().decode("utf-8")
    (STATUS_DIR / "status-http.json").write_text(http_payload, encoding="utf-8")
except Exception:
    pass
PY
  '';
in {
  imports = [ ../_template/exercise-base ];

  proxmoxLXC.manageNetwork = lib.mkForce true;
  services.openssh.ports = lib.mkForce [ 2222 ];

  environment.etc."proxnix-orb-probe".text = "orb-probe-ok\\n";

  proxnix._internal.configTemplateSources = {
    public-test-site = pkgs.writeText "proxnix-public-test-site.txt" ''
      shared={{ secrets.shared-file }}
      group={{ secrets.group-file }}
      container={{ secrets.container-file }}
      config={{ secrets.public-config-secret }}
      literal={{ values.literal }}
      enabled={{ values.enabled }}
      number={{ values.number }}
    '';
    public-create-only = pkgs.writeText "proxnix-public-create-only.txt" ''
      public-create-only={{ secrets.public-create-only-secret }}
    '';
  };

  proxnix.secrets.shared-file = {
    source = {
      scope = "shared";
      name = "shared_secret";
    };
    file = {};
  };

  proxnix.secrets.group-file = {
    source = {
      scope = "group";
      group = "exercise-group";
      name = "group_secret";
    };
    file = {};
  };

  proxnix.secrets.container-file = {
    source = {
      scope = "container";
      name = "container_secret";
    };
    file = {};
  };

  proxnix.secrets.public-env = {
    source.name = "env_secret";
    env = {
      service = "proxnix-test-site-public-runtime.service";
      variable = "EXERCISE_PUBLIC_ENV_SECRET";
    };
  };

  proxnix.secrets.public-credential = {
    source.name = "credential_secret";
    credential = {
      service = "proxnix-test-site-public-runtime.service";
      name = "exercise-public-credential";
    };
  };

  proxnix.secrets.public-config-secret = {
    source.name = "public_config_secret";
  };

  proxnix.secrets.public-create-only-secret = {
    source.name = "public_create_only_secret";
  };

  proxnix.configs.public-rendered = {
    source = "public-test-site";
    path = "/var/lib/proxnix-test-site/public-config.txt";
    secretValues = [
      "shared-file"
      "group-file"
      "container-file"
      "public-config-secret"
    ];
    values = {
      literal = "literal-__TOKEN__";
      enabled = true;
      number = 7;
    };
  };

  proxnix.configs.public-create-only = {
    source = "public-create-only";
    path = "/var/lib/proxnix-test-site/public-create-only.txt";
    createOnly = true;
    secretValues = [ "public-create-only-secret" ];
  };

  proxnix._internal.secrets.files.service-secret = {
    secret = "service_secret";
    lifecycle = "service";
    service = "proxnix-test-site-service-reader.service";
    path = "/run/proxnix-test-site/service-secret.txt";
    owner = "root";
    group = "root";
    mode = "0400";
  };

  proxnix._internal.secrets.templates.internal-template = {
    source = pkgs.writeText "proxnix-test-site-internal-template.txt" ''
      shared=__SHARED__
      group=__GROUP__
      container=__CONTAINER__
      override=__OVERRIDE__
    '';
    destination = "/var/lib/proxnix-test-site/internal-template.txt";
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

  proxnix._internal.secrets.templates.service-template = {
    lifecycle = "service";
    service = "proxnix-test-site-service-reader.service";
    source = pkgs.writeText "proxnix-test-site-service-template.txt" ''
      service=__SERVICE__
    '';
    destination = "/run/proxnix-test-site/service-template.txt";
    owner = "root";
    group = "root";
    mode = "0400";
    substitutions = {
      "__SERVICE__" = { secret = "service_secret"; };
    };
  };

  proxnix._internal.secrets.templates.create-only-template = {
    source = pkgs.writeText "proxnix-test-site-create-only.txt" ''
      create-only=__CREATE_ONLY__
    '';
    destination = "/var/lib/proxnix-test-site/create-only.txt";
    owner = "root";
    group = "root";
    mode = "0600";
    createOnly = true;
    wantedBy = [ "multi-user.target" ];
    substitutions = {
      "__CREATE_ONLY__" = { secret = "create_only_secret"; };
    };
  };

  proxnix._internal.secrets.oneshot.test-site-oneshot = {
    secret = "oneshot_secret";
    wantedBy = [ "multi-user.target" ];
    runtimeInputs = [ pkgs.coreutils ];
    script = ''
      install -d -m 0755 /var/lib/proxnix-test-site
      install -m 0600 "$PROXNIX_SECRET_FILE" /var/lib/proxnix-test-site/oneshot-secret.txt
    '';
  };

  systemd.services.proxnix-test-site-service-reader = {
    description = "Read service-lifetime proxnix test-site secrets";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "simple";
      ExecStart = serviceReader;
      Restart = "always";
      RestartSec = "2s";
    };
  };

  systemd.services.proxnix-test-site-public-runtime = {
    description = "Read proxnix public env and credential secrets";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "simple";
      ExecStart = publicRuntimeReader;
      Restart = "always";
      RestartSec = "2s";
    };
  };

  virtualisation.podman.enable = true;
  virtualisation.containers.storage.settings.storage.driver = lib.mkForce "vfs";

  systemd.services.proxnix-test-site-podman = {
    description = "Run proxnix Podman secret driver test-site exercise";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = podmanCheck;
    };
  };

  systemd.services.proxnix-test-site-status = {
    description = "Publish proxnix test-site status page";
    wantedBy = [ "multi-user.target" ];
    after = [
      "nginx.service"
      "proxnix-test-site-service-reader.service"
      "proxnix-test-site-public-runtime.service"
      "proxnix-test-site-podman.service"
      "proxnix-test-site-attached.service"
    ];
    wants = [
      "nginx.service"
      "proxnix-test-site-service-reader.service"
      "proxnix-test-site-public-runtime.service"
      "proxnix-test-site-podman.service"
      "proxnix-test-site-attached.service"
    ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = statusWriter;
    };
  };

  systemd.services.proxnix-test-site-attached = {
    description = "Attached test-site unit";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.coreutils}/bin/true";
      RemainAfterExit = true;
    };
  };
}
"""


ATTACHED_HELPER_SH = """\
#!/run/current-system/sw/bin/bash
set -eu
printf 'helper-sh-ok\\n'
"""


ATTACHED_HELPER_PY = """\
#!/run/current-system/sw/bin/python3
print("helper-py-ok")
"""


def replace_placeholder(text: str, token: str) -> str:
    return text.replace("__TOKEN__", token)


def build_expected_assertions(token: str) -> list[tuple[str, str, object]]:
    return [
        ("template marker present", "template_marker", "exercise-base-template"),
        ("site import rendered", "site_marker", "test-site-ok"),
        ("orb probe dropin rendered", "probe_marker", "orb-probe-ok"),
        ("attached service loaded", "attached_service_load_state", "loaded"),
        ("apply service loaded", "apply_service_load_state", "loaded"),
        ("public runtime secret unit loaded", "public_runtime_secret_unit_load_state", "loaded"),
        ("internal oneshot unit loaded", "oneshot_unit_load_state", "loaded"),
        ("podman exercise unit loaded", "podman_service_load_state", "loaded"),
        ("attached shell helper executable", "helper_sh_output", "helper-sh-ok"),
        ("attached python helper executable", "helper_py_output", "helper-py-ok"),
        ("shared secret visible", "shared_secret", f"shared-{token}"),
        ("group secret visible", "group_secret", f"group-{token}"),
        ("container secret visible", "container_secret", f"container-{token}"),
        ("container overrides shared", "override_secret", f"container-override-{token}"),
        ("public shared file delivered", "public_shared_file", f"shared-{token}"),
        ("public group file delivered", "public_group_file", f"group-{token}"),
        ("public container file delivered", "public_container_file", f"container-{token}"),
        ("public env delivered", "public_env_value", f"env-{token}"),
        ("public credential delivered", "public_credential_value", f"credential-{token}"),
        ("public runtime service started", "public_runtime_marker", "public-runtime-ready"),
        (
            "public config rendered",
            "public_rendered_config",
            "\n".join(
                [
                    f"shared=shared-{token}",
                    f"group=group-{token}",
                    f"container=container-{token}",
                    f"config=config-secret-{token}",
                    f"literal=literal-{token}",
                    "enabled=true",
                    "number=7",
                ]
            ),
        ),
        ("public createOnly config rendered", "public_create_only_config", f"public-create-only=public-create-only-{token}"),
        ("internal oneshot materialized", "oneshot_secret", f"oneshot-{token}"),
        (
            "internal activation template rendered",
            "activation_template",
            "\n".join(
                [
                    f"shared=shared-{token}",
                    f"group=group-{token}",
                    f"container=container-{token}",
                    f"override=container-override-{token}",
                ]
            ),
        ),
        ("internal service reader marker written", "service_reader_marker", "service-reader-ready"),
        ("internal service secret snapshot", "service_secret_snapshot", f"service-{token}"),
        ("internal service template snapshot", "service_template_snapshot", f"service=service-{token}"),
        ("internal createOnly template rendered", "create_only_template", f"create-only=create-only-{token}"),
        ("podman secret materialized in workload", "podman_secret", f"podman-{token}"),
        ("podman secret driver lists secret", "podman_secret_list_contains_secret", True),
        ("podman info captured", "podman_info_present", True),
    ]


def render_test_site(site_dir: Path, *, vmid: str, token: str) -> None:
    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)

    write_text(site_dir / "site.nix", SITE_NIX)
    write_text(site_dir / "containers" / "_template" / "exercise-base" / "default.nix", TEMPLATE_DEFAULT_NIX)
    write_text(site_dir / "containers" / vmid / "templates" / "exercise-base.template", "")
    write_text(
        site_dir / "containers" / vmid / "secret-groups.list",
        "exercise-group\npodman-group\n",
    )
    write_text(
        site_dir / "containers" / vmid / "dropins" / "00-test-site.nix",
        replace_placeholder(TEST_SITE_DROPIN_NIX, token),
    )
    write_text(
        site_dir / "containers" / vmid / "dropins" / "proxnix-test-site-report.sh",
        ATTACHED_HELPER_SH,
        mode=0o755,
    )
    write_text(
        site_dir / "containers" / vmid / "dropins" / "proxnix-test-site-helper.py",
        ATTACHED_HELPER_PY,
        mode=0o755,
    )


def seed_test_site_secrets(config: "WorkstationConfig", *, vmid: str, token: str) -> None:
    from .secrets_cli import (
        container_recipients,
        ensure_container_identity,
        ensure_host_relay_identity,
        group_recipients,
        shared_recipients,
        sops_set_local,
    )
    from .sops_ops import ensure_private_permissions

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

    ensure_container_identity(config, site_paths, vmid)
    container_recips = container_recipients(config, site_paths, vmid)
    container_values = {
        "container_secret": f"container-{token}",
        "override_secret": f"container-override-{token}",
        "service_secret": f"service-{token}",
        "create_only_secret": f"create-only-{token}",
        "env_secret": f"env-{token}",
        "credential_secret": f"credential-{token}",
        "public_config_secret": f"config-secret-{token}",
        "public_create_only_secret": f"public-create-only-{token}",
    }
    for name, value in container_values.items():
        sops_set_local(config, site_paths, site_paths.container_store(vmid), container_recips, name, value)


def write_text(path: Path, text: str, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
