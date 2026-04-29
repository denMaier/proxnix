import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PVE_CONF_TO_NIX = ROOT / "host" / "runtime" / "lib" / "pve-conf-to-nix.py"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class PveConfToNixTests(unittest.TestCase):
    def test_delegates_to_rust_host_binary_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = root / "proxnix-host"
            marker = root / "args.txt"
            write_executable(
                fake_bin,
                f"""#!/bin/sh
printf '%s\\n' "$@" > '{marker}'
""",
            )

            env = os.environ.copy()
            env["PROXNIX_HOST_BIN"] = str(fake_bin)
            result = subprocess.run(
                [
                    sys.executable,
                    str(PVE_CONF_TO_NIX),
                    "--pve-conf",
                    str(root / "101.conf"),
                    "--out-dir",
                    str(root / "out"),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                marker.read_text(encoding="utf-8").splitlines(),
                [
                    "pve-conf-to-nix",
                    "--pve-conf",
                    str(root / "101.conf"),
                    "--out-dir",
                    str(root / "out"),
                ],
            )

    def test_fails_when_configured_host_binary_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            env = os.environ.copy()
            env["PROXNIX_HOST_BIN"] = str(root / "missing-proxnix-host")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PVE_CONF_TO_NIX),
                    "--pve-conf",
                    str(root / "101.conf"),
                    "--out-dir",
                    str(root / "out"),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

            self.assertEqual(result.returncode, 127)
            self.assertIn("proxnix-host not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
