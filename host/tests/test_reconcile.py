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


if __name__ == "__main__":
    unittest.main()
