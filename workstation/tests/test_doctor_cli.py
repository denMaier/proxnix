from __future__ import annotations

import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from proxnix_workstation.config import WorkstationConfig
from proxnix_workstation.doctor_cli import DoctorReporter, lint_site_repo
from proxnix_workstation.paths import SitePaths
from proxnix_workstation.publish_cli import PublishOptions


class _FakeNamedProvider:
    def describe(self) -> str:
        return "pykeepass"

    def has_any(self, _ref) -> bool:
        return False


def _test_config(site_dir: Path) -> WorkstationConfig:
    return WorkstationConfig(
        config_file=Path("/tmp/proxnix-config"),
        site_dir=site_dir,
        hosts=(),
        ssh_identity=None,
        remote_dir=PurePosixPath("/var/lib/proxnix"),
        remote_priv_dir=PurePosixPath("/var/lib/proxnix/private"),
        remote_host_relay_identity=PurePosixPath("/etc/proxnix/host_relay_identity"),
        secret_provider="pykeepass",
        secret_provider_command=None,
        scripts_dir=None,
        provider_environment=(),
    )


class DoctorCliTests(unittest.TestCase):
    def test_non_embedded_provider_does_not_report_missing_private_site_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_dir = Path(temp_dir)
            (site_dir / "containers").mkdir()
            config = _test_config(site_dir)
            site_paths = SitePaths(site_dir=site_dir)
            reporter = DoctorReporter()
            options = PublishOptions(dry_run=True, report_changes=True, config_only=True)

            with patch(
                "proxnix_workstation.doctor_cli.load_secret_provider",
                return_value=_FakeNamedProvider(),
            ), patch(
                "proxnix_workstation.doctor_cli.collect_site_vmids",
                return_value=[],
            ), patch(
                "proxnix_workstation.doctor_cli.build_publish_tree",
                return_value=None,
            ):
                lint_site_repo(config, site_paths, options, reporter, site_dir / ".tmp")

            rendered = reporter.render()
            self.assertNotIn("no private site dir yet", rendered)
            self.assertNotIn("private site dir present", rendered)


if __name__ == "__main__":
    unittest.main()
