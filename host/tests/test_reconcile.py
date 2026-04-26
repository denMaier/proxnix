import os
import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


RECONCILE = Path(__file__).resolve().parents[1] / "runtime" / "bin" / "proxnix-reconcile"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class ReconcileDryRunTests(unittest.TestCase):
    def test_dry_run_prints_planned_actions_for_selected_vmid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","system":"/nix/store/system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}}}}
JSON
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--dry-run", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "101 build /nix/store/system-101",
                    "101 keep existing CT",
                    "101 seed desired closure",
                    "101 activate desired system",
                ],
            )

    def test_dry_run_rejects_missing_selected_vmid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(fake_bin / "nix", "#!/bin/sh\nprintf '%s\\n' '{\"containers\":{}}'\n")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(Path(tmp) / "pve" / "lxc"),
                    "PROXNIX_RUN_DIR": str(Path(tmp) / "run"),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--dry-run", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("VMID 101 is not present", result.stderr)

    def test_build_only_writes_status_without_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","sourceRevision":{"commit":"abc123"},"system":"/nix/store/eval-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}}}}
JSON
    ;;
  build)
    printf '%s\n' /nix/store/built-system-101
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--build-only", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 built /nix/store/built-system-101")
            status = (status_dir / "101.json").read_text(encoding="utf-8")
            self.assertIn('"desiredSystem": "/nix/store/built-system-101"', status)
            self.assertIn('"lastBuildStatus": "ok"', status)
            self.assertIn('"lastDeployStatus": "not-run"', status)
            self.assertIn('"currentSystem": null', status)

    def test_seed_only_imports_closure_and_marks_status_seeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            fake_bin.mkdir()
            status_dir.mkdir(parents=True)
            (status_dir / "101.json").write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/built-system-101",
                        "currentSystem": None,
                        "previousSystem": None,
                        "lastBuildStatus": "ok",
                        "lastDeployStatus": "not-run",
                        "lastError": None,
                    }
                ),
                encoding="utf-8",
            )

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix-store",
                """#!/bin/sh
case "$1" in
  --query)
    printf '%s\n' /nix/store/dep-a /nix/store/built-system-101
    ;;
  --export)
    printf '%s\n' exported-closure
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "pct",
                """#!/bin/sh
if [ "$1" != "exec" ]; then
  exit 2
fi
if [ "$4" = "nix-store" ] && [ "$5" = "--import" ]; then
  cat >/dev/null
  exit 0
fi
if [ "$4" = "test" ] && [ "$5" = "-x" ]; then
  exit 0
fi
exit 2
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_RUN_DIR": str(run_dir),
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--seed-only", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 seeded /nix/store/built-system-101")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["lastDeployStatus"], "seeded")
            self.assertIsNone(status["lastError"])

    def test_seed_only_records_failed_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            fake_bin.mkdir()
            status_dir.mkdir(parents=True)
            (status_dir / "101.json").write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/built-system-101",
                        "lastBuildStatus": "ok",
                        "lastDeployStatus": "not-run",
                    }
                ),
                encoding="utf-8",
            )

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(fake_bin / "nix-store", "#!/bin/sh\nprintf '%s\\n' /nix/store/built-system-101\n")
            write_executable(fake_bin / "pct", "#!/bin/sh\necho import failed >&2\nexit 1\n")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_RUN_DIR": str(run_dir),
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--seed-only", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 2)
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["lastDeployStatus"], "failed")
            self.assertIn("closure seed failed", status["lastError"])

    def test_full_reconcile_activates_and_records_previous_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            marker = Path(tmp) / "activated"
            status_dir = root / "status"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","sourceRevision":{"commit":"abc123"},"system":"/nix/store/eval-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}}}}
JSON
    ;;
  build)
    printf '%s\n' /nix/store/built-system-101
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "nix-store",
                """#!/bin/sh
case "$1" in
  --query)
    printf '%s\n' /nix/store/dep-a /nix/store/built-system-101
    ;;
  --export)
    printf '%s\n' exported-closure
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "pct",
                f"""#!/bin/sh
if [ "$1" = "status" ]; then
  printf '%s\\n' 'status: running'
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    if [ -f {marker} ]; then
      printf '%s\\n' /nix/store/built-system-101
    else
      printf '%s\\n' /nix/store/old-system-101
    fi
    ;;
  nix-store)
    cat >/dev/null
    ;;
  test)
    exit 0
    ;;
  /nix/store/built-system-101/bin/switch-to-configuration)
    touch {marker}
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 activated /nix/store/built-system-101")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["desiredSystem"], "/nix/store/built-system-101")
            self.assertEqual(status["currentSystem"], "/nix/store/built-system-101")
            self.assertEqual(status["previousSystem"], "/nix/store/old-system-101")
            self.assertEqual(status["lastDeployStatus"], "ok")
            self.assertIsNone(status["lastError"])

    def test_recreate_missing_calls_create_lxc_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            marker = Path(tmp) / "activated"
            create_args = Path(tmp) / "create-args"
            fake_create = Path(tmp) / "proxnix-create-lxc"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","sourceRevision":null,"system":"/nix/store/eval-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101","memory":2048,"swap":512,"cores":2,"rootfs":"local-lvm:vm-101-disk-0,size=8G","net0":"name=eth0,bridge=vmbr0,ip=dhcp","unprivileged":true}}}}
JSON
    ;;
  build)
    printf '%s\n' /nix/store/built-system-101
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "nix-store",
                "#!/bin/sh\ncase \"$1\" in --query) printf '%s\\n' /nix/store/built-system-101 ;; --export) printf exported ;; *) exit 2 ;; esac\n",
            )
            write_executable(
                fake_create,
                f"""#!/bin/sh
printf '%s\\n' "$@" > {create_args}
mkdir -p {pve}
printf '%s\\n' 'ostype: nixos' 'hostname: ct101' > {pve / "101.conf"}
""",
            )
            write_executable(
                fake_bin / "pct",
                f"""#!/bin/sh
if [ "$1" = "status" ]; then
  printf '%s\\n' 'status: stopped'
  exit 0
fi
if [ "$1" = "start" ]; then
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    if [ -f {marker} ]; then
      printf '%s\\n' /nix/store/built-system-101
    else
      printf '%s\\n' /nix/store/old-system-101
    fi
    ;;
  nix-store)
    cat >/dev/null
    ;;
  test)
    exit 0
    ;;
  /nix/store/built-system-101/bin/switch-to-configuration)
    touch {marker}
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                    "PROXNIX_CREATE_LXC": str(fake_create),
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--vmid", "101", "--recreate-missing"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            args = create_args.read_text(encoding="utf-8").splitlines()
            self.assertIn("--no-doctor", args)
            self.assertIn("--no-start", args)
            self.assertIn("local-lvm", args)
            self.assertIn("8", args)
            self.assertIn("vmbr0", args)
            self.assertIn("dhcp", args)

    def test_rollback_activates_previous_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            marker = Path(tmp) / "rolled-back"
            status_dir = root / "status"
            fake_bin.mkdir()
            status_dir.mkdir(parents=True)
            (status_dir / "101.json").write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/new-system-101",
                        "currentSystem": "/nix/store/new-system-101",
                        "previousSystem": "/nix/store/old-system-101",
                        "lastBuildStatus": "ok",
                        "lastDeployStatus": "ok",
                    }
                ),
                encoding="utf-8",
            )

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix-store",
                """#!/bin/sh
case "$1" in
  --query)
    printf '%s\n' /nix/store/old-system-101
    ;;
  --export)
    printf '%s\n' exported-closure
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "pct",
                f"""#!/bin/sh
if [ "$1" = "status" ]; then
  printf '%s\\n' 'status: running'
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    if [ -f {marker} ]; then
      printf '%s\\n' /nix/store/old-system-101
    else
      printf '%s\\n' /nix/store/new-system-101
    fi
    ;;
  nix-store)
    cat >/dev/null
    ;;
  test)
    exit 0
    ;;
  /nix/store/old-system-101/bin/switch-to-configuration)
    touch {marker}
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_RUN_DIR": str(run_dir),
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--rollback", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 rolled back /nix/store/old-system-101")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["currentSystem"], "/nix/store/old-system-101")
            self.assertEqual(status["lastDeployStatus"], "rollback-ok")
            self.assertIsNone(status["lastError"])


if __name__ == "__main__":
    unittest.main()
