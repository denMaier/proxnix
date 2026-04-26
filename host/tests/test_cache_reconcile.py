import os
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CACHE_RECONCILE = ROOT / "host" / "runtime" / "bin" / "proxnix-cache-reconcile"
STATE_CLI = ROOT / "host" / "runtime" / "bin" / "proxnix-reconciler-state"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def add_pending_closure(db: Path, store_path: str, gcroot: Path) -> None:
    result = subprocess.run(
        [
            str(STATE_CLI),
            "--db",
            str(db),
            "observe-closure",
            "--store-path",
            store_path,
            "--host-has-closure",
            "true",
            "--shared-cache-has-closure",
            "false",
            "--pending-cache-upload",
            "true",
            "--protected-by-host-gc-root",
            "true",
            "--gc-root-path",
            str(gcroot),
        ],
        check=False,
        text=True,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)


class CacheReconcileTests(unittest.TestCase):
    def test_successful_upload_clears_pending_state_and_releases_gcroot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "reconciler.sqlite"
            fake_bin = Path(tmp) / "bin"
            gcroot = Path(tmp) / "gcroots" / "101-desired"
            log = Path(tmp) / "nix-log"
            store_path = "/nix/store/desired-system-101"
            fake_bin.mkdir()
            gcroot.parent.mkdir()
            gcroot.symlink_to(store_path)
            add_pending_closure(db, store_path, gcroot)

            write_executable(
                fake_bin / "nix",
                f"""#!/bin/sh
printf '%s\\n' "$@" >> {log}
if [ "$1" = "path-info" ] && [ "$2" = "{store_path}" ]; then
  printf '%s\\n' "{store_path}"
  exit 0
fi
if [ "$1" = "copy" ] && [ "$2" = "--to" ]; then
  exit 0
fi
if [ "$1" = "path-info" ] && [ "$2" = "--store" ]; then
  printf '%s\\n' "{store_path}"
  exit 0
fi
exit 2
""",
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            result = subprocess.run(
                [
                    str(CACHE_RECONCILE),
                    "--db",
                    str(db),
                    "--cache-store",
                    "ssh://cache.example",
                ],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"{store_path} cache-uploaded", result.stdout)
            self.assertFalse(gcroot.exists())
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "select pending_cache_upload, shared_cache_has_closure, protected_by_host_gc_root from closure_observations where store_path = ?",
                    (store_path,),
                ).fetchone()
                attempt = conn.execute(
                    "select status from deployment_attempts where store_path = ?",
                    (store_path,),
                ).fetchone()
            self.assertEqual(row, (0, 1, 0))
            self.assertEqual(attempt, ("cache-uploaded",))

    def test_failed_upload_keeps_pending_state_and_gcroot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "reconciler.sqlite"
            fake_bin = Path(tmp) / "bin"
            gcroot = Path(tmp) / "gcroots" / "101-desired"
            store_path = "/nix/store/desired-system-101"
            fake_bin.mkdir()
            gcroot.parent.mkdir()
            gcroot.symlink_to(store_path)
            add_pending_closure(db, store_path, gcroot)

            write_executable(
                fake_bin / "nix",
                f"""#!/bin/sh
if [ "$1" = "path-info" ] && [ "$2" = "{store_path}" ]; then
  exit 0
fi
if [ "$1" = "copy" ]; then
  printf '%s\\n' 'upload failed' >&2
  exit 1
fi
exit 2
""",
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            result = subprocess.run(
                [
                    str(CACHE_RECONCILE),
                    "--db",
                    str(db),
                    "--cache-store",
                    "ssh://cache.example",
                ],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 1)
            self.assertTrue(gcroot.is_symlink())
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "select pending_cache_upload, protected_by_host_gc_root from closure_observations where store_path = ?",
                    (store_path,),
                ).fetchone()
                attempt = conn.execute(
                    "select status, error from deployment_attempts where store_path = ?",
                    (store_path,),
                ).fetchone()
            self.assertEqual(row, (1, 1))
            self.assertEqual(attempt[0], "cache-upload-blocked")
            self.assertIn("nix copy failed", attempt[1])

    def test_missing_host_closure_records_blocked_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "reconciler.sqlite"
            fake_bin = Path(tmp) / "bin"
            gcroot = Path(tmp) / "gcroots" / "101-desired"
            store_path = "/nix/store/missing-system-101"
            fake_bin.mkdir()
            gcroot.parent.mkdir()
            gcroot.symlink_to(store_path)
            add_pending_closure(db, store_path, gcroot)

            write_executable(
                fake_bin / "nix",
                "#!/bin/sh\nprintf '%s\\n' 'missing path' >&2\nexit 1\n",
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            result = subprocess.run(
                [
                    str(CACHE_RECONCILE),
                    "--db",
                    str(db),
                    "--cache-store",
                    "ssh://cache.example",
                ],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 1)
            self.assertTrue(gcroot.is_symlink())
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "select pending_cache_upload from closure_observations where store_path = ?",
                    (store_path,),
                ).fetchone()
                attempt = conn.execute(
                    "select status, error from deployment_attempts where store_path = ?",
                    (store_path,),
                ).fetchone()
            self.assertEqual(row, (1,))
            self.assertEqual(attempt[0], "cache-upload-blocked")
            self.assertIn("host closure missing", attempt[1])


if __name__ == "__main__":
    unittest.main()
