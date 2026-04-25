from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proxnix_workstation import tui


class TuiCoreTests(unittest.TestCase):
    def test_load_config_reads_manager_config_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config"
            config.write_text(
                "\n".join(
                    [
                        "PROXNIX_SITE_DIR='/srv/proxnix-site'",
                        "PROXNIX_HOSTS='root@node1 root@node2'",
                        "PROXNIX_SECRET_PROVIDER='exec'",
                        "PROXNIX_SECRET_PROVIDER_COMMAND='pass proxnix'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(tui, "CONFIG_FILE", config):
                loaded = tui.load_config()

            self.assertEqual(loaded.site_dir, "/srv/proxnix-site")
            self.assertEqual(loaded.hosts, ["root@node1", "root@node2"])
            self.assertEqual(loaded.secret_provider, "exec")
            self.assertEqual(loaded.secret_provider_command, "pass proxnix")

    def test_save_config_preserves_newer_manager_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config"
            config.write_text(
                "\n".join(
                    [
                        "PROXNIX_SITE_DIR='/old/site'",
                        "PROXNIX_PROXMOX_API_ENABLED='true'",
                        "PROXNIX_PROXMOX_API_URL='https://pve.example:8006'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            app = tui.AppState()
            app.config = tui.Config(
                site_dir="/new/site",
                hosts=["root@node1"],
                secret_provider="embedded-sops",
            )

            with patch.object(tui, "CONFIG_FILE", config):
                tui._save_config(None, app)  # type: ignore[arg-type]

            text = config.read_text(encoding="utf-8")
            self.assertIn("PROXNIX_SITE_DIR='/new/site'", text)
            self.assertIn("PROXNIX_HOSTS='root@node1'", text)
            self.assertIn("PROXNIX_PROXMOX_API_ENABLED='true'", text)
            self.assertIn("PROXNIX_PROXMOX_API_URL='https://pve.example:8006'", text)

    def test_scan_site_uses_manager_status_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site = root / "site"
            container = site / "containers" / "120"
            (container / "dropins").mkdir(parents=True)
            (container / "dropins" / "web.nix").write_text("{ ... }: {}\n", encoding="utf-8")
            (container / "secret-groups.list").write_text("db\n", encoding="utf-8")
            (site / "private" / "containers" / "120").mkdir(parents=True)
            (site / "private" / "containers" / "120" / "age_identity.sops.yaml").write_text("x\n", encoding="utf-8")
            config = root / "config"
            config.write_text(f"PROXNIX_SITE_DIR='{site}'\n", encoding="utf-8")

            with patch.object(tui, "CONFIG_FILE", config):
                containers = tui.scan_site(str(site))

            self.assertEqual(len(containers), 1)
            self.assertEqual(containers[0].vmid, "120")
            self.assertEqual(containers[0].dropins, ["web.nix"])
            self.assertEqual(containers[0].groups, ["db"])
            self.assertTrue(containers[0].has_identity)


if __name__ == "__main__":
    unittest.main()
