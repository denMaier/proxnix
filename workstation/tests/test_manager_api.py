from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from proxnix_workstation.cli import main as cli_main
from proxnix_workstation.manager_api import attach_secret_group, build_config_state, build_status
from proxnix_workstation.manager_api import create_container_bundle, create_secret_group, create_site_nix
from proxnix_workstation.manager_api import delete_container_bundle, detach_secret_group, save_config, set_config_value


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
            sidebar_state = root / "manager-sidebar-state.json"
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
            sidebar_state.write_text(
                json.dumps(
                    {
                        "sites": {
                            str(site.resolve()): {
                                "containers": {
                                    "120": {
                                        "displayName": "web",
                                        "group": "apps",
                                        "labels": ["prod", ""],
                                    }
                                }
                            }
                        }
                    }
                ),
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
            self.assertEqual(
                status["sidebarMetadata"],
                {"120": {"displayName": "web", "group": "apps", "labels": ["prod"]}},
            )

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

    def test_config_state_and_save_preserve_provider_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config"
            config.write_text(
                "\n".join(
                    [
                        "PROXNIX_SITE_DIR='/old/site'",
                        "PROXNIX_PYKEEPASS_DATABASE='~/secrets/proxnix.kdbx'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            state = save_config(config, {"siteDir": "/new/site", "secretProvider": "pykeepass"})

            self.assertTrue(state["changed"])
            self.assertEqual(state["config"]["siteDir"], "/new/site")
            self.assertEqual(state["config"]["secretProvider"], "pykeepass")
            self.assertEqual(state["preservedKeys"], ["PROXNIX_PYKEEPASS_DATABASE"])
            text = config.read_text(encoding="utf-8")
            self.assertIn("PROXNIX_SITE_DIR='/new/site'", text)
            self.assertIn("PROXNIX_PYKEEPASS_DATABASE='~/secrets/proxnix.kdbx'", text)

    def test_set_config_value_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config"
            with self.assertRaises(ValueError):
                set_config_value(config, "unknown", "value")

    def test_config_get_json_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config"
            config.write_text("PROXNIX_SITE_DIR='/tmp/site'\n", encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                exit_code = cli_main(["--config", str(config), "config", "get", "siteDir"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["key"], "siteDir")
            self.assertEqual(payload["data"]["value"], "/tmp/site")

    def test_config_set_json_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config"

            output = StringIO()
            with redirect_stdout(output):
                exit_code = cli_main(
                    ["--config", str(config), "config", "set", "siteDir", "/tmp/site", "--json"]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["config"]["siteDir"], "/tmp/site")
            self.assertTrue(build_config_state(config)["exists"])

    def test_site_group_mutations_update_secret_group_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site = root / "site"
            (site / "containers" / "120").mkdir(parents=True)
            config = root / "config"
            config.write_text(f"PROXNIX_SITE_DIR='{site}'\n", encoding="utf-8")

            create_secret_group(config, "db")
            attach_secret_group(config, "120", "db")
            self.assertEqual((site / "containers" / "120" / "secret-groups.list").read_text(encoding="utf-8"), "db\n")

            detach_secret_group(config, "120", "db")
            self.assertFalse((site / "containers" / "120" / "secret-groups.list").exists())

    def test_create_site_nix_writes_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site = root / "site"
            site.mkdir()
            config = root / "config"
            config.write_text(f"PROXNIX_SITE_DIR='{site}'\n", encoding="utf-8")

            status = create_site_nix(config)

            self.assertTrue((site / "site.nix").is_file())
            self.assertTrue(status["siteNixExists"])

    def test_container_create_rolls_back_when_identity_creation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site = root / "site"
            site.mkdir()
            config = root / "config"
            config.write_text(f"PROXNIX_SITE_DIR='{site}'\n", encoding="utf-8")

            with patch("proxnix_workstation.manager_api.load_secret_provider", return_value=object()), patch(
                "proxnix_workstation.manager_api.initialize_container_identity",
                side_effect=RuntimeError("identity failed"),
            ):
                with self.assertRaises(RuntimeError):
                    create_container_bundle(config, "120")

            self.assertFalse((site / "containers" / "120").exists())

    def test_container_delete_removes_scaffold_when_no_local_secrets(self) -> None:
        class FakeProvider:
            def list_names(self, _ref):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site = root / "site"
            (site / "containers" / "120").mkdir(parents=True)
            config = root / "config"
            config.write_text(f"PROXNIX_SITE_DIR='{site}'\n", encoding="utf-8")

            with patch("proxnix_workstation.manager_api.load_secret_provider", return_value=FakeProvider()), patch(
                "proxnix_workstation.manager_api.have_container_private_key",
                return_value=False,
            ):
                status = delete_container_bundle(config, "120")

            self.assertFalse((site / "containers" / "120").exists())
            self.assertEqual(status["containers"], [])

    def test_site_group_attach_cli_returns_status_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site = root / "site"
            (site / "containers" / "120").mkdir(parents=True)
            config = root / "config"
            config.write_text(f"PROXNIX_SITE_DIR='{site}'\n", encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                exit_code = cli_main(["--config", str(config), "site", "group", "attach", "120", "db"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["attachedSecretGroups"], ["db"])


if __name__ == "__main__":
    unittest.main()
