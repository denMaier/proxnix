import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


GC = Path(__file__).resolve().parents[1] / "runtime" / "bin" / "proxnix-gc"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class ProxnixGcTests(unittest.TestCase):
    def test_prunes_stage_dirs_and_only_stale_desired_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            gcroot_dir = root / "gcroots" / "deploy"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            (run_dir / "101").mkdir(parents=True)
            (run_dir / "202").mkdir(parents=True)
            gcroot_dir.mkdir(parents=True)
            os.symlink("/nix/store/golden", gcroot_dir / "golden-template")
            os.symlink("/nix/store/desired-101", gcroot_dir / "101-desired")
            os.symlink("/nix/store/desired-202", gcroot_dir / "202-desired")
            os.symlink("/nix/store/other", gcroot_dir / "not-managed")

            write_executable(
                fake_bin / "pct",
                """#!/bin/sh
if [ "$1" = status ] && [ "$2" = 101 ]; then
  printf '%s\n' 'status: running'
  exit 0
fi
if [ "$1" = status ] && [ "$2" = 202 ]; then
  exit 2
fi
exit 2
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_GCROOT_DIR": str(gcroot_dir),
                }
            )

            result = subprocess.run(
                [str(GC)],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((run_dir / "101").exists())
            self.assertFalse((run_dir / "202").exists())
            self.assertTrue((gcroot_dir / "golden-template").is_symlink())
            self.assertTrue((gcroot_dir / "101-desired").is_symlink())
            self.assertFalse((gcroot_dir / "202-desired").exists())
            self.assertTrue((gcroot_dir / "not-managed").is_symlink())
            self.assertIn(
                "released stage dir for booted CT 101 (content already copied into guest)",
                result.stderr,
            )

    def test_dry_run_does_not_remove_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            gcroot_dir = root / "gcroots" / "deploy"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            (run_dir / "202").mkdir(parents=True)
            gcroot_dir.mkdir(parents=True)
            os.symlink("/nix/store/desired-202", gcroot_dir / "202-desired")
            write_executable(fake_bin / "pct", "#!/bin/sh\nexit 2\n")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_GCROOT_DIR": str(gcroot_dir),
                }
            )

            result = subprocess.run(
                [str(GC), "--dry-run"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((run_dir / "202").is_dir())
            self.assertTrue((gcroot_dir / "202-desired").is_symlink())


if __name__ == "__main__":
    unittest.main()
