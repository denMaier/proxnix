import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class HostPackagingTests(unittest.TestCase):
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
