from __future__ import annotations

import unittest
from pathlib import Path, PurePosixPath
from subprocess import CompletedProcess
from unittest.mock import patch

from proxnix_workstation.config import WorkstationConfig
from proxnix_workstation.publish_cli import do_rsync


class _FakeSession:
    def __init__(self, host: str = "root@node1") -> None:
        self.host = host

    def rsync_ssh_command(self) -> str:
        return "ssh -o BatchMode=yes"


def _test_config() -> WorkstationConfig:
    return WorkstationConfig(
        config_file=Path("/tmp/proxnix-config"),
        site_dir=None,
        master_identity=Path("/tmp/id_master"),
        hosts=("root@node1",),
        ssh_identity=None,
        remote_dir=PurePosixPath("/var/lib/proxnix"),
        remote_priv_dir=PurePosixPath("/var/lib/proxnix/private"),
        remote_host_relay_identity=PurePosixPath("/etc/proxnix/host_relay_identity"),
    )


class PublishCliTests(unittest.TestCase):
    def test_do_rsync_preserves_trailing_slash_for_directory_contents(self) -> None:
        session = _FakeSession()
        config = _test_config()
        calls: list[list[str]] = []

        def fake_run_command(args, **kwargs):
            calls.append(list(args))
            return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with patch("proxnix_workstation.publish_cli.run_command", side_effect=fake_run_command):
            do_rsync(
                session,
                config,
                Path("/tmp/relay/containers"),
                PurePosixPath("/var/lib/proxnix/containers"),
                directory_contents=True,
                delete=True,
                dry_run=True,
                report=[],
            )

        self.assertEqual(
            calls[0][-2:],
            ["/tmp/relay/containers/", "root@node1:/var/lib/proxnix/containers"],
        )

    def test_do_rsync_keeps_file_source_without_trailing_slash(self) -> None:
        session = _FakeSession()
        config = _test_config()
        calls: list[list[str]] = []

        def fake_run_command(args, **kwargs):
            calls.append(list(args))
            return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with patch("proxnix_workstation.publish_cli.run_command", side_effect=fake_run_command):
            do_rsync(
                session,
                config,
                Path("/tmp/relay/site.nix"),
                PurePosixPath("/var/lib/proxnix/site.nix"),
                delete=False,
                dry_run=True,
                report=[],
            )

        self.assertEqual(
            calls[0][-2:],
            ["/tmp/relay/site.nix", "root@node1:/var/lib/proxnix/site.nix"],
        )


if __name__ == "__main__":
    unittest.main()
