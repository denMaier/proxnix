import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class HostPackagingTests(unittest.TestCase):
    def test_full_reconcile_timer_is_not_packaged_or_enabled(self) -> None:
        package_common = (ROOT / "host" / "packaging" / "package-common.sh").read_text(
            encoding="utf-8"
        )
        install_sh = (ROOT / "host" / "install" / "install.sh").read_text(encoding="utf-8")
        postinst = (ROOT / "host" / "packaging" / "debian" / "postinst").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("systemd/proxnix-reconcile.timer", package_common)
        self.assertNotIn('do_systemd_timer "proxnix-reconcile"', install_sh)
        self.assertNotIn("enable --now proxnix-reconcile.timer", postinst)
        self.assertIn('disable_legacy_timer "proxnix-reconcile"', install_sh)
        self.assertIn("disable --now proxnix-reconcile.timer", postinst)

    def test_reconcile_services_are_packaged_and_prestart_builds(self) -> None:
        package_common = (ROOT / "host" / "packaging" / "package-common.sh").read_text(
            encoding="utf-8"
        )
        install_sh = (ROOT / "host" / "install" / "install.sh").read_text(encoding="utf-8")
        prestart = (
            ROOT / "host" / "runtime" / "lxc" / "hooks" / "nixos-proxnix-prestart"
        ).read_text(encoding="utf-8")
        mount = (
            ROOT / "host" / "runtime" / "lxc" / "hooks" / "nixos-proxnix-mount"
        ).read_text(encoding="utf-8")
        template_service = (
            ROOT / "host" / "runtime" / "systemd" / "proxnix-reconcile@.service"
        ).read_text(encoding="utf-8")

        self.assertIn("systemd/proxnix-reconcile.service", package_common)
        self.assertIn("systemd/proxnix-reconcile@.service", package_common)
        self.assertIn("bin/proxnix-gc", package_common)
        self.assertIn("bin/proxnix-reconcile-build-golden", package_common)
        self.assertIn("bin/proxnix-reconcile-build", package_common)
        self.assertIn("bin/proxnix-reconcile-seed", package_common)
        self.assertIn("bin/proxnix-reconcile-seed-offline", package_common)
        self.assertIn("bin/proxnix-reconcile-activate", package_common)
        self.assertIn("proxnix-reconcile-build-golden", install_sh)
        self.assertIn("proxnix-gc", install_sh)
        self.assertIn("proxnix-reconcile-build", install_sh)
        self.assertIn("proxnix-reconcile-seed", install_sh)
        self.assertIn("proxnix-reconcile-seed-offline", install_sh)
        self.assertIn("proxnix-reconcile-activate", install_sh)
        self.assertIn('do_systemd_service "proxnix-reconcile"', install_sh)
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
        self.assertIn("ExecStart=/usr/local/sbin/proxnix-reconcile --vmid %i", template_service)
        self.assertIn("ExecStartPre=/bin/sleep 10", template_service)

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

    def test_shared_cache_reconcile_runtime_is_not_packaged(self) -> None:
        package_common = (ROOT / "host" / "packaging" / "package-common.sh").read_text(
            encoding="utf-8"
        )
        install_sh = (ROOT / "host" / "install" / "install.sh").read_text(encoding="utf-8")
        uninstall_sh = (ROOT / "host" / "install" / "uninstall.sh").read_text(
            encoding="utf-8"
        )
        postinst = (ROOT / "host" / "packaging" / "debian" / "postinst").read_text(
            encoding="utf-8"
        )
        prerm = (ROOT / "host" / "packaging" / "debian" / "prerm").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("proxnix-cache-reconcile", package_common)
        self.assertNotIn("proxnix-cache-reconcile", install_sh)
        self.assertNotIn("proxnix-cache-reconcile", uninstall_sh)
        self.assertNotIn("proxnix-cache-reconcile", postinst)
        self.assertNotIn("proxnix-cache-reconcile", prerm)

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
