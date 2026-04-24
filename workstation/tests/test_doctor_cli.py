from __future__ import annotations

import tempfile
import unittest
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from proxnix_workstation.config import WorkstationConfig
from proxnix_workstation.doctor_cli import DoctorReporter, lint_site_repo
from proxnix_workstation.doctor_cli import main as doctor_main
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

    def test_doctor_json_reports_structured_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_dir = Path(temp_dir) / "site"
            (site_dir / "containers").mkdir(parents=True)
            config_path = Path(temp_dir) / "config"
            config_path.write_text(
                f"PROXNIX_SITE_DIR='{site_dir}'\nPROXNIX_SECRET_PROVIDER='pykeepass'\n",
                encoding="utf-8",
            )

            def fake_build_tree(_config, _site_paths, _options, root: Path) -> None:
                (root / "containers").mkdir(parents=True)

            output = StringIO()
            with patch(
                "proxnix_workstation.doctor_cli.need_publish_tools",
                return_value=SitePaths(site_dir=site_dir),
            ), patch(
                "proxnix_workstation.doctor_cli.load_secret_provider",
                return_value=_FakeNamedProvider(),
            ), patch(
                "proxnix_workstation.doctor_cli.build_publish_tree",
                side_effect=fake_build_tree,
            ), redirect_stdout(output):
                exit_code = doctor_main(["--config", str(config_path), "--site-only", "--config-only", "--json"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["exitCode"], 0)
            self.assertEqual(payload["data"]["sections"][0]["heading"], "site")


if __name__ == "__main__":
    unittest.main()
