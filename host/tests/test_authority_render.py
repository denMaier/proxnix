import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "runtime" / "lib" / "proxnix_authority_render.py"
SPEC = importlib.util.spec_from_file_location("proxnix_authority_render", MODULE_PATH)
assert SPEC is not None
authority_render = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = authority_render
SPEC.loader.exec_module(authority_render)


class AuthorityRenderTests(unittest.TestCase):
    def test_render_authority_from_legacy_relay_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            authority = root / "authority"
            (root / "containers" / "101" / "dropins").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (root / "site.nix").write_text("{ ... }: {}\n", encoding="utf-8")
            (root / "publish-revision.json").write_text(
                '{"commit":"abc123","branch":"main","dirtyWorktreeIgnored":false}\n',
                encoding="utf-8",
            )
            (root / "containers" / "101" / "dropins" / "workload.nix").write_text(
                "{ ... }: {}\n",
                encoding="utf-8",
            )
            (pve / "101.conf").write_text(
                "\n".join(
                    [
                        "ostype: nixos",
                        "hostname: ct101",
                        "memory: 2048",
                        "swap: 512",
                        "cores: 2",
                        "rootfs: local-lvm:vm-101-disk-0,size=8G",
                        "net0: name=eth0,bridge=vmbr0,ip=dhcp",
                        "unprivileged: 1",
                        "features: nesting=1,keyctl=1",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            manifests = authority_render.render_authority(root, authority, pve, "pve1")

            self.assertEqual([manifest.vmid for manifest in manifests], ["101"])
            self.assertTrue((authority / "flake.nix").is_file())
            self.assertTrue((authority / "modules" / "proxnix-guest-base.nix").is_file())
            self.assertTrue((authority / "generated" / "legacy" / "site.nix").is_file())
            self.assertTrue((authority / "generated" / "containers" / "101" / "proxmox.nix").is_file())

            modules_nix = (authority / "generated" / "containers" / "101" / "modules.nix").read_text(
                encoding="utf-8"
            )
            self.assertIn("../../../modules/proxnix-guest-base.nix", modules_nix)
            self.assertIn("../../legacy/site.nix", modules_nix)
            self.assertIn("./dropins/workload.nix", modules_nix)

            manifest_nix = (authority / "generated" / "node-manifest.nix").read_text(encoding="utf-8")
            self.assertIn('nodeName = "pve1";', manifest_nix)
            self.assertIn('"101" = {', manifest_nix)
            self.assertIn('systemAttr = "nixosConfigurations.ct101.config.system.build.toplevel";', manifest_nix)
            self.assertIn('hostname = "ct101";', manifest_nix)
            self.assertIn("memory = 2048;", manifest_nix)
            self.assertIn("unprivileged = true;", manifest_nix)
            self.assertIn("localVmids = [", manifest_nix)
            self.assertIn("local = false;", manifest_nix)
            self.assertIn("observedPveConfig = true;", manifest_nix)
            flake_nix = (authority / "flake.nix").read_text(encoding="utf-8")
            self.assertIn("proxnix.containers = manifest.containers;", flake_nix)

    def test_keeps_cluster_container_without_matching_local_pve_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            authority = root / "authority"
            (root / "containers" / "101").mkdir(parents=True)
            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")

            manifests = authority_render.render_authority(root, authority, Path(tmp) / "missing-pve", "pve1")

            self.assertEqual([manifest.vmid for manifest in manifests], ["101"])
            manifest_nix = (authority / "generated" / "node-manifest.nix").read_text(encoding="utf-8")
            self.assertIn("vmids = [", manifest_nix)
            self.assertIn("containers = {", manifest_nix)
            self.assertIn('"101" = {', manifest_nix)
            self.assertIn("local = false;", manifest_nix)
            self.assertIn("observedPveConfig = false;", manifest_nix)


if __name__ == "__main__":
    unittest.main()
