from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from proxnix_workstation.orb_site_cli import (
    LOCAL_SITE_OVERRIDE,
    build_remote_site_script,
    build_site_container,
    prepare_local_site_tree,
)


class OrbSiteCliTests(unittest.TestCase):
    def test_prepare_local_site_tree_keeps_targeted_container_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_site = temp_path / "source-site"
            target_site = temp_path / "target-site"

            (source_site / "site.nix").parent.mkdir(parents=True, exist_ok=True)
            (source_site / "site.nix").write_text("{ ... }: {}\n", encoding="utf-8")
            (source_site / "containers" / "_template" / "base.nix").parent.mkdir(parents=True, exist_ok=True)
            (source_site / "containers" / "_template" / "base.nix").write_text("{ ... }: {}\n", encoding="utf-8")
            (source_site / "containers" / "132" / "dropins" / "10-site.nix").parent.mkdir(parents=True, exist_ok=True)
            (source_site / "containers" / "132" / "dropins" / "10-site.nix").write_text("{ ... }: {}\n", encoding="utf-8")
            (source_site / "containers" / "999" / "dropins" / "10-other.nix").parent.mkdir(parents=True, exist_ok=True)
            (source_site / "containers" / "999" / "dropins" / "10-other.nix").write_text("{ ... }: {}\n", encoding="utf-8")
            (source_site / "private" / "shared" / "secrets.sops.yaml").parent.mkdir(parents=True, exist_ok=True)
            (source_site / "private" / "shared" / "secrets.sops.yaml").write_text("shared: value\n", encoding="utf-8")
            (source_site / "private" / "groups" / "g1" / "secrets.sops.yaml").parent.mkdir(parents=True, exist_ok=True)
            (source_site / "private" / "groups" / "g1" / "secrets.sops.yaml").write_text("group: value\n", encoding="utf-8")
            (source_site / "private" / "containers" / "132" / "secrets.sops.yaml").parent.mkdir(parents=True, exist_ok=True)
            (source_site / "private" / "containers" / "132" / "secrets.sops.yaml").write_text("container: value\n", encoding="utf-8")
            (source_site / "private" / "containers" / "999" / "secrets.sops.yaml").parent.mkdir(parents=True, exist_ok=True)
            (source_site / "private" / "containers" / "999" / "secrets.sops.yaml").write_text("other: value\n", encoding="utf-8")
            (source_site / "private" / "host_relay_identity.sops.yaml").write_text("identity: value\n", encoding="utf-8")

            prepare_local_site_tree(source_site, target_site, vmid="132")

            self.assertTrue((target_site / "site.nix").is_file())
            self.assertTrue((target_site / "containers" / "_template" / "base.nix").is_file())
            self.assertTrue((target_site / "containers" / "132" / "dropins" / "10-site.nix").is_file())
            self.assertFalse((target_site / "containers" / "999").exists())
            self.assertTrue((target_site / "private" / "shared" / "secrets.sops.yaml").is_file())
            self.assertTrue((target_site / "private" / "groups" / "g1" / "secrets.sops.yaml").is_file())
            self.assertTrue((target_site / "private" / "containers" / "132" / "secrets.sops.yaml").is_file())
            self.assertFalse((target_site / "private" / "containers" / "999").exists())
            self.assertTrue((target_site / "private" / "host_relay_identity.sops.yaml").is_file())

            override_text = (target_site / "containers" / "132" / "dropins" / "00-proxnix-orb-local.nix").read_text(
                encoding="utf-8"
            )

        self.assertEqual(LOCAL_SITE_OVERRIDE, override_text)
        self.assertIn("proxmoxLXC.manageNetwork = lib.mkForce true;", override_text)
        self.assertIn("services.openssh.ports = lib.mkForce [ 2222 ];", override_text)

    def test_remote_site_script_uses_prestart_and_persistent_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            container = build_site_container("132", "proxnix-local")

            rendered = build_remote_site_script(
                repo_root=temp_path / "repo",
                relay_tree=temp_path / "relay",
                pve_conf_path=temp_path / "ct.conf",
                bootstrap_config_path=temp_path / "bootstrap.nix",
                container=container,
                timeout_seconds=90,
                keep_running=True,
            )

        self.assertIn('PRESTART_HOOK="${REPO_ROOT}/host/lxc/hooks/nixos-proxnix-prestart"', rendered)
        self.assertIn('bash "${PRESTART_HOOK}" --vmid "${VMID}" --pve-conf "${PVE_CONF}"', rendered)
        self.assertIn('local_nixos_container_apply_prestart_stage "/run/proxnix/${VMID}"', rendered)
        self.assertNotIn("nixos-proxnix-mount", rendered)
        self.assertIn('NSPAWN_SERVICE_UNIT=', rendered)
        self.assertIn('--unit "${NSPAWN_SERVICE_UNIT}"', rendered)
        self.assertIn('RUN_STATE_FILE="${ORB_STATE_ROOT}/current-run.json"', rendered)
        self.assertIn('cleanup_previous_run() {', rendered)
        self.assertIn('if [ "${KEEP_RUNNING}" = "1" ]; then', rendered)
        self.assertIn('"guest_machine": sys.argv[2],', rendered)
        self.assertIn('"keep_running": sys.argv[4] == "1",', rendered)
