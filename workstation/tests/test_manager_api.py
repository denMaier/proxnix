from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from proxnix_workstation.cli import main as cli_main
from proxnix_workstation.manager_api import build_status


class ManagerApiTests(unittest.TestCase):
    def test_build_status_reports_site_containers_and_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site = root / "site"
            container = site / "containers" / "120"
            (container / "dropins").mkdir(parents=True)
            (container / "dropins" / "web.nix").write_text("{ ... }: {}\n", encoding="utf-8")
            (container / "secret-groups.list").write_text("db\n", encoding="utf-8")
            (site / "private" / "groups" / "db").mkdir(parents=True)
            (site / "private" / "containers" / "120").mkdir(parents=True)
            (site / "private" / "containers" / "120" / "age_identity.sops.yaml").write_text("x\n", encoding="utf-8")
            (site / "site.nix").write_text("{ ... }: {}\n", encoding="utf-8")
            config = root / "config"
            config.write_text(
                "\n".join(
                    [
                        f"PROXNIX_SITE_DIR='{site}'",
                        "PROXNIX_HOSTS='root@node1 root@node2'",
                        "PROXNIX_SECRET_PROVIDER='embedded-sops'",
                        "PROXNIX_SOPS_MASTER_IDENTITY='~/.ssh/proxnix-master'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            status = build_status(config)

            self.assertTrue(status["configExists"])
            self.assertTrue(status["siteDirExists"])
            self.assertEqual(status["definedSecretGroups"], ["db"])
            self.assertEqual(status["attachedSecretGroups"], ["db"])
            self.assertEqual(status["siteNixContent"], "{ ... }: {}\n")
            self.assertEqual(status["preservedConfigKeys"], ["PROXNIX_SOPS_MASTER_IDENTITY"])
            self.assertEqual(status["config"]["hosts"], "root@node1 root@node2")
            self.assertEqual(status["containers"][0]["vmid"], "120")
            self.assertEqual(status["containers"][0]["dropins"], ["web.nix"])
            self.assertTrue(status["containers"][0]["hasIdentity"])

    def test_status_json_cli_uses_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config"
            config.write_text("", encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                exit_code = cli_main(["--config", str(config), "status", "--json"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertIsNone(payload["error"])
            self.assertIn("config", payload["data"])
            self.assertEqual(payload["data"]["configPath"], str(config))
            self.assertEqual(payload["warnings"], ["Set PROXNIX_SITE_DIR to scan your site repo."])


if __name__ == "__main__":
    unittest.main()
