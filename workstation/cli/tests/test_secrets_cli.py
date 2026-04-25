from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from proxnix_workstation.secrets_cli import main as secrets_main


class SecretsCliJsonTests(unittest.TestCase):
    def test_status_json_reports_missing_site_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config"
            config.write_text("", encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                exit_code = secrets_main(["--config", str(config), "status", "--json"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["provider"], "embedded-sops")
            self.assertEqual(payload["data"]["warnings"], ["Set PROXNIX_SITE_DIR to scan your site repo."])

    def test_scope_status_json_reports_missing_site_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config"
            config.write_text("", encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                exit_code = secrets_main(
                    ["--config", str(config), "scope-status", "--scope", "shared", "--json"]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["scopeType"], "shared")
            self.assertEqual(payload["data"]["entries"], [])
            self.assertEqual(payload["data"]["warnings"], ["Set site directory first."])

    def test_set_shared_json_suppresses_human_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config"
            config.write_text("", encoding="utf-8")
            output = StringIO()

            with patch("proxnix_workstation.secrets_cli.cmd_set_shared", return_value=0), redirect_stdout(output):
                exit_code = secrets_main(["--config", str(config), "set-shared", "api_token", "--json"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["output"], "Set secret api_token.")


if __name__ == "__main__":
    unittest.main()
