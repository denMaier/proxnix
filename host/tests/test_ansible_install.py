import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class AnsibleInstallTests(unittest.TestCase):
    def test_ansible_playbook_installs_reconciler_runtime(self) -> None:
        playbook = (ROOT / "host" / "deploy" / "ansible" / "install.yml").read_text(
            encoding="utf-8"
        )
        prestart = (
            ROOT / "host" / "runtime" / "lxc" / "hooks" / "nixos-proxnix-prestart"
        ).read_text(encoding="utf-8")
        mount = (
            ROOT / "host" / "runtime" / "lxc" / "hooks" / "nixos-proxnix-mount"
        ).read_text(encoding="utf-8")

        self.assertIn("nix --version", playbook)
        self.assertIn('nix --extra-experimental-features "nix-command flakes" eval --expr true', playbook)
        self.assertIn("proxnix_authority_render.py", playbook)
        self.assertIn("proxnix_reconciler_state.py", playbook)
        self.assertIn("proxnix-authority-render", playbook)
        self.assertIn("proxnix-gc", playbook)
        self.assertIn("proxnix-reconcile", playbook)
        self.assertIn("proxnix-reconcile-build-golden", playbook)
        self.assertIn("proxnix-reconcile-build", playbook)
        self.assertIn("proxnix-reconcile-seed", playbook)
        self.assertIn("proxnix-reconcile-seed-offline", playbook)
        self.assertIn("proxnix-reconcile-activate", playbook)
        self.assertIn("proxnix-reconciler-state", playbook)
        self.assertIn("proxnix-reconcile.service", playbook)
        self.assertIn("proxnix-reconcile@.service", playbook)
        self.assertIn("proxnix-reconcile.timer", playbook)
        self.assertIn("state: stopped", playbook)

        self.assertIn('PROXNIX_PRESTART_GOLDEN_BUILD:-1', prestart)
        self.assertIn('proxnix-reconcile-build-golden', prestart)
        self.assertIn('PROXNIX_PRESTART_BUILD:-1', prestart)
        self.assertIn('proxnix-reconcile-build --vmid "$VMID"', prestart)
        self.assertNotIn('copy/etc/nixos/configuration.nix', prestart)
        self.assertNotIn("PROXNIX_PRESTART_RECONCILE", prestart)
        self.assertNotIn('systemctl start --no-block "proxnix-reconcile@${VMID}.service"', prestart)
        self.assertIn('proxnix-reconcile-seed-offline --vmid "$VMID" --rootfs "$ROOTFS"', mount)
        self.assertIn("rsync -a --delete", mount)
        self.assertIn("/var/lib/proxnix/build-input", mount)
        self.assertNotIn('copy_guest_file "${COPY_ETC_NIXOS_DIR}/configuration.nix"', mount)
        self.assertNotIn('bind_ro_dir "${BIND_CONFIG_DIR}" "${PROXNIX_CONFIG_DIR}"', mount)

    def test_gc_service_uses_runtime_helper(self) -> None:
        service = (
            ROOT / "host" / "runtime" / "systemd" / "proxnix-gc.service"
        ).read_text(encoding="utf-8")
        timer = (
            ROOT / "host" / "runtime" / "systemd" / "proxnix-gc.timer"
        ).read_text(encoding="utf-8")

        self.assertIn("ExecStart=/usr/local/sbin/proxnix-gc", service)
        self.assertIn("stale proxnix host state", service)
        self.assertIn("stale proxnix host state", timer)

    def test_guest_boot_activation_unit_is_in_base_nix(self) -> None:
        base_nix = (ROOT / "host" / "runtime" / "nix" / "base.nix").read_text(
            encoding="utf-8"
        )

        self.assertIn('writeShellScriptBin "proxnix-boot-activate"', base_nix)
        self.assertIn('systemd.services.proxnix-boot-activate', base_nix)
        self.assertIn('next-system', base_nix)
        self.assertIn('previous-system', base_nix)
        self.assertIn('activation-failed-system', base_nix)
        self.assertIn('switch-to-configuration" switch', base_nix)
        self.assertIn('before = [ "multi-user.target" ]', base_nix)


if __name__ == "__main__":
    unittest.main()
