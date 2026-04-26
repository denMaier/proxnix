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

    def test_reconcile_services_are_packaged_and_prestart_triggered(self) -> None:
        package_common = (ROOT / "host" / "packaging" / "package-common.sh").read_text(
            encoding="utf-8"
        )
        install_sh = (ROOT / "host" / "install" / "install.sh").read_text(encoding="utf-8")
        prestart = (
            ROOT / "host" / "runtime" / "lxc" / "hooks" / "nixos-proxnix-prestart"
        ).read_text(encoding="utf-8")
        template_service = (
            ROOT / "host" / "runtime" / "systemd" / "proxnix-reconcile@.service"
        ).read_text(encoding="utf-8")

        self.assertIn("systemd/proxnix-reconcile.service", package_common)
        self.assertIn("systemd/proxnix-reconcile@.service", package_common)
        self.assertIn('do_systemd_service "proxnix-reconcile"', install_sh)
        self.assertIn('systemctl start --no-block "proxnix-reconcile@${VMID}.service"', prestart)
        self.assertIn("ExecStart=/usr/local/sbin/proxnix-reconcile --vmid %i", template_service)
        self.assertIn("ExecStartPre=/bin/sleep 10", template_service)

    def test_cache_reconcile_systemd_units_are_packaged_and_installed(self) -> None:
        package_common = (ROOT / "host" / "packaging" / "package-common.sh").read_text(
            encoding="utf-8"
        )
        install_sh = (ROOT / "host" / "install" / "install.sh").read_text(encoding="utf-8")
        uninstall_sh = (ROOT / "host" / "install" / "uninstall.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("systemd/proxnix-cache-reconcile.service", package_common)
        self.assertIn("systemd/proxnix-cache-reconcile.timer", package_common)
        self.assertIn('do_systemd_timer "proxnix-cache-reconcile"', install_sh)
        self.assertIn("proxnix-cache-reconcile.timer", uninstall_sh)
        self.assertIn("proxnix-cache-reconcile.service", uninstall_sh)

    def test_cache_reconcile_service_runs_command(self) -> None:
        service = (
            ROOT / "host" / "runtime" / "systemd" / "proxnix-cache-reconcile.service"
        ).read_text(encoding="utf-8")
        timer = (
            ROOT / "host" / "runtime" / "systemd" / "proxnix-cache-reconcile.timer"
        ).read_text(encoding="utf-8")

        self.assertIn("ExecStart=/usr/local/sbin/proxnix-cache-reconcile", service)
        self.assertIn("EnvironmentFile=-/etc/proxnix/cache-reconcile.env", service)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()
