import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATE_CLI = ROOT / "host" / "runtime" / "bin" / "proxnix-reconciler-state"


class ReconcilerStateTests(unittest.TestCase):
    def test_init_creates_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state" / "reconciler.sqlite"

            result = subprocess.run(
                [str(STATE_CLI), "--db", str(db), "init"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with sqlite3.connect(db) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "select name from sqlite_master where type = 'table'"
                    )
                }
            self.assertIn("container_observations", tables)
            self.assertIn("closure_observations", tables)
            self.assertIn("deployment_attempts", tables)

    def test_observation_updates_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "reconciler.sqlite"
            cmd = [
                str(STATE_CLI),
                "--db",
                str(db),
                "observe-container",
                "--vmid",
                "101",
                "--node",
                "pve1",
                "--desired-system",
                "/nix/store/desired-a",
                "--current-system",
                "/nix/store/current-a",
                "--container-is-local",
                "true",
                "--last-phase",
                "observe",
                "--last-status",
                "noop-current",
            ]

            first = subprocess.run(cmd, check=False, text=True, stderr=subprocess.PIPE)
            updated_cmd = list(cmd)
            updated_cmd[-1] = "activated"
            second = subprocess.run(
                updated_cmd,
                check=False,
                text=True,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            with sqlite3.connect(db) as conn:
                rows = conn.execute(
                    "select vmid, last_status from container_observations"
                ).fetchall()
            self.assertEqual(rows, [(101, "activated")])

    def test_closure_observation_records_local_gc_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "reconciler.sqlite"
            result = subprocess.run(
                [
                    str(STATE_CLI),
                    "--db",
                    str(db),
                    "observe-closure",
                    "--store-path",
                    "/nix/store/aaa-desired",
                    "--host-has-closure",
                    "true",
                    "--container-has-closure",
                    "false",
                    "--protected-by-host-gc-root",
                    "true",
                    "--gc-root-path",
                    "/var/lib/proxnix/gcroots/deploy/aaa-desired",
                ],
                check=False,
                text=True,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "select store_path, host_has_closure, container_has_closure, protected_by_host_gc_root, gc_root_path from closure_observations"
                ).fetchone()
            self.assertEqual(
                row,
                (
                    "/nix/store/aaa-desired",
                    1,
                    0,
                    1,
                    "/var/lib/proxnix/gcroots/deploy/aaa-desired",
                ),
            )


if __name__ == "__main__":
    sys.exit(unittest.main())
