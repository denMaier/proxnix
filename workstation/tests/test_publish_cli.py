from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path, PurePosixPath
from subprocess import CompletedProcess
from unittest.mock import patch

from proxnix_workstation.config import WorkstationConfig
from proxnix_workstation.paths import SitePaths
from proxnix_workstation.publish_cli import (
    PublishOptions,
    PublishSource,
    do_rsync,
    materialize_head_site,
    should_report_change,
    write_publish_revision,
)


class _FakeSession:
    def __init__(self, host: str = "root@node1") -> None:
        self.host = host

    def rsync_ssh_command(self) -> str:
        return "ssh -o BatchMode=yes"


def _test_config() -> WorkstationConfig:
    return WorkstationConfig(
        config_file=Path("/tmp/proxnix-config"),
        site_dir=None,
        hosts=("root@node1",),
        ssh_identity=None,
        remote_dir=PurePosixPath("/var/lib/proxnix"),
        remote_priv_dir=PurePosixPath("/var/lib/proxnix/private"),
        remote_host_relay_identity=PurePosixPath("/etc/proxnix/host_relay_identity"),
        secret_provider="embedded-sops",
        secret_provider_command=None,
        provider_environment=(("PROXNIX_SOPS_MASTER_IDENTITY", "/tmp/id_master"),),
    )


class PublishCliTests(unittest.TestCase):
    def test_materialize_head_site_ignores_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            site = root / "site"
            site.mkdir()
            (site / "containers" / "100").mkdir(parents=True)
            (site / "site.nix").write_text("committed\n", encoding="utf-8")
            (site / "containers" / "100" / "config.nix").write_text("old\n", encoding="utf-8")

            subprocess.run(["git", "-C", str(site), "init"], check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-C", str(site), "config", "user.email", "test@example.invalid"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", str(site), "config", "user.name", "Test User"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(["git", "-C", str(site), "add", "-A"], check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-C", str(site), "commit", "-m", "initial"],
                check=True,
                capture_output=True,
                text=True,
            )
            commit = subprocess.run(
                ["git", "-C", str(site), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            (site / "site.nix").write_text("dirty\n", encoding="utf-8")
            (site / "containers" / "100" / "untracked.nix").write_text("new\n", encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                source = materialize_head_site(_test_config(), SitePaths(site), root / "snapshot")

            self.assertEqual(source.commit, commit)
            self.assertTrue(source.dirty)
            self.assertTrue(source.using_head)
            self.assertEqual((source.site_paths.site_nix).read_text(encoding="utf-8"), "committed\n")
            self.assertFalse((source.site_paths.container_dir("100") / "untracked.nix").exists())

    def test_materialize_head_site_limits_target_vmid_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            site = root / "site"
            (site / "containers" / "100").mkdir(parents=True)
            (site / "containers" / "200").mkdir(parents=True)
            (site / "private" / "containers" / "100").mkdir(parents=True)
            (site / "private" / "containers" / "200").mkdir(parents=True)
            (site / "containers" / "100" / "config.nix").write_text("target\n", encoding="utf-8")
            (site / "containers" / "200" / "config.nix").write_text("other\n", encoding="utf-8")
            (site / "private" / "containers" / "100" / "secrets.sops.yaml").write_text("target\n", encoding="utf-8")
            (site / "private" / "containers" / "200" / "secrets.sops.yaml").write_text("other\n", encoding="utf-8")
            (site / "site.nix").write_text("site\n", encoding="utf-8")

            subprocess.run(["git", "-C", str(site), "init"], check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-C", str(site), "config", "user.email", "test@example.invalid"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", str(site), "config", "user.name", "Test User"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(["git", "-C", str(site), "add", "-A"], check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-C", str(site), "commit", "-m", "initial"],
                check=True,
                capture_output=True,
                text=True,
            )

            with contextlib.redirect_stdout(io.StringIO()):
                source = materialize_head_site(
                    _test_config(),
                    SitePaths(site),
                    root / "snapshot",
                    PublishOptions(target_vmid="100"),
                )

            self.assertTrue((source.site_paths.container_dir("100") / "config.nix").is_file())
            self.assertTrue((source.site_paths.container_identity_store("100").parent / "secrets.sops.yaml").is_file())
            self.assertFalse(source.site_paths.container_dir("200").exists())
            self.assertFalse(source.site_paths.container_identity_store("200").parent.exists())
            self.assertFalse(source.site_paths.site_nix.exists())

    def test_materialize_head_site_omits_private_for_external_secret_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            site = root / "site"
            (site / "containers" / "100").mkdir(parents=True)
            (site / "private" / "containers" / "100").mkdir(parents=True)
            (site / "containers" / "100" / "config.nix").write_text("target\n", encoding="utf-8")
            (site / "private" / "containers" / "100" / "secrets.sops.yaml").write_text("embedded\n", encoding="utf-8")

            subprocess.run(["git", "-C", str(site), "init"], check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-C", str(site), "config", "user.email", "test@example.invalid"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", str(site), "config", "user.name", "Test User"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(["git", "-C", str(site), "add", "-A"], check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-C", str(site), "commit", "-m", "initial"],
                check=True,
                capture_output=True,
                text=True,
            )

            config = replace(_test_config(), secret_provider="pass")
            with contextlib.redirect_stdout(io.StringIO()):
                source = materialize_head_site(config, SitePaths(site), root / "snapshot", PublishOptions())

            self.assertTrue(source.site_paths.container_dir("100").is_dir())
            self.assertFalse((source.site_paths.site_dir / "private").exists())

    def test_write_publish_revision_records_commit_and_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "publish-revision.json"

            write_publish_revision(
                PublishSource(
                    site_paths=SitePaths(Path(temp)),
                    commit="1234567890abcdef",
                    branch="main",
                    dirty=True,
                    using_head=True,
                ),
                path,
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["commit"], "1234567890abcdef")
            self.assertEqual(payload["branch"], "main")
            self.assertTrue(payload["dirty_worktree_ignored"])
            self.assertEqual(payload["source"], "git-head")

    def test_revision_marker_is_reported_as_publish_change(self) -> None:
        config = _test_config()

        self.assertTrue(should_report_change(config, PurePosixPath("/var/lib/proxnix/publish-revision.json")))

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
