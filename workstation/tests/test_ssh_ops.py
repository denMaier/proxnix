from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from subprocess import CompletedProcess
from unittest.mock import patch

from proxnix_workstation.config import WorkstationConfig
from proxnix_workstation.ssh_ops import SSHSession


class _FixedTempDir:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> str:
        self.path.mkdir(parents=True, exist_ok=True)
        return str(self.path)

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def _test_config() -> WorkstationConfig:
    return WorkstationConfig(
        config_file=Path("/tmp/proxnix-config"),
        site_dir=None,
        master_identity=Path("/tmp/id_master"),
        hosts=("root@node1",),
        ssh_identity=Path("/tmp/id_publish"),
        remote_dir=PurePosixPath("/var/lib/proxnix"),
        remote_priv_dir=PurePosixPath("/var/lib/proxnix/private"),
        remote_host_relay_identity=PurePosixPath("/etc/proxnix/host_relay_identity"),
        secret_provider="embedded-sops",
        secret_provider_command=None,
    )


class SSHSessionTests(unittest.TestCase):
    def test_enter_failure_cleans_temp_dir(self) -> None:
        config = _test_config()
        with tempfile.TemporaryDirectory() as temp_dir:
            socket_root = Path(temp_dir) / "ssh-temp"
            fixed_temp_dir = _FixedTempDir(socket_root)
            with patch("proxnix_workstation.ssh_ops.tempfile.TemporaryDirectory", return_value=fixed_temp_dir):
                with patch("proxnix_workstation.ssh_ops.run_command", side_effect=RuntimeError("boom")):
                    with self.assertRaises(RuntimeError):
                        with SSHSession(config, "root@node1"):
                            pass
            self.assertFalse(socket_root.exists())

    def test_run_and_rsync_use_noninteractive_control_socket_args(self) -> None:
        config = _test_config()
        calls: list[list[str]] = []

        def fake_run_command(args, **kwargs):
            call = list(args)
            calls.append(call)
            if "ControlMaster=yes" in call:
                control_path = next(
                    value.split("=", 1)[1]
                    for value in call
                    if value.startswith("ControlPath=")
                )
                Path(control_path).touch()
            return CompletedProcess(args=call, returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_root:
            with patch("proxnix_workstation.ssh_ops.run_command", side_effect=fake_run_command):
                with SSHSession(config, "root@node1", temp_root=Path(temp_root)) as session:
                    session.run("true")
                    rsync_command = session.rsync_ssh_command()

        self.assertIn("BatchMode=yes", calls[0])
        self.assertIn("ControlMaster=yes", calls[0])
        self.assertIn("BatchMode=yes", calls[1])
        self.assertIn("ControlMaster=no", calls[1])
        self.assertIn("-O", calls[2])
        self.assertIn("exit", calls[2])
        self.assertIn("-i", calls[0])
        self.assertIn("BatchMode=yes", rsync_command)
        self.assertIn("ControlMaster=no", rsync_command)


if __name__ == "__main__":
    unittest.main()
