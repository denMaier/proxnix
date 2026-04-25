from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from proxnix_workstation.exercise_cli import (
    BASELINE_SCRIPT,
    ExerciseContainer,
    NIXOS_BASH,
    RunReport,
    add_status_document_assertions,
    build_containers,
    build_existing_container_cleanup_command,
    ensure_container_slots_available,
    guest_debug_units,
    parse_pct_config_hostname,
    render_site_fixture,
    replace_nixos_shell_placeholder,
    wait_for_apply,
)
from proxnix_workstation.errors import ProxnixWorkstationError


class _FakeSession:
    def __init__(self, host: str = "root@node1") -> None:
        self.host = host


class ExerciseCliTests(unittest.TestCase):
    def test_parse_pct_config_hostname(self) -> None:
        config_text = "arch: amd64\nhostname: proxnix-exercise-baseline\nmemory: 3072\n"
        self.assertEqual(parse_pct_config_hostname(config_text), "proxnix-exercise-baseline")

    def test_baseline_script_uses_nixos_shell(self) -> None:
        rendered = replace_nixos_shell_placeholder(BASELINE_SCRIPT)
        self.assertTrue(rendered.startswith(f"#!{NIXOS_BASH}\n"))

    def test_render_site_fixture_includes_status_page_services(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_dir = Path(temp_dir) / "site"
            containers = build_containers(940)
            render_site_fixture(site_dir, containers, "tok123")

            template_default = (site_dir / "containers" / "_template" / "exercise-base" / "default.nix").read_text(
                encoding="utf-8"
            )
            baseline_nix = (site_dir / "containers" / "940" / "dropins" / "exercise.nix").read_text(
                encoding="utf-8"
            )
            attached_service = site_dir / "containers" / "940" / "dropins" / "proxnix-baseline-attached.service"

        self.assertIn('services.nginx = {', template_default)
        self.assertIn('root = "/var/lib/proxnix-exercise/www";', template_default)
        self.assertEqual(len(containers), 1)
        self.assertIn('proxnix-exercise-baseline-status', baseline_nix)
        self.assertIn('proxnix-baseline-attached', baseline_nix)
        self.assertIn('shared-tok123', baseline_nix)
        self.assertIn('service=service-tok123', baseline_nix)
        self.assertIn('podman-tok123', baseline_nix)
        self.assertIn('proxnix-exercise-service-reader', baseline_nix)
        self.assertIn('proxnix-exercise-podman', baseline_nix)
        self.assertFalse(attached_service.exists())

    def test_add_status_document_assertions_tracks_failed_check(self) -> None:
        report = RunReport(started_at="2026-04-18T00:00:00+00:00")
        add_status_document_assertions(
            report,
            "baseline",
            {
                "status": "failed",
                "checks": [
                    {
                        "name": "attached script executable",
                        "ok": False,
                        "actual": "boom",
                        "expected": "baseline-script-ok",
                    }
                ],
            },
        )

        self.assertEqual(report.status, "failed")
        self.assertEqual(len(report.assertions), 2)
        self.assertEqual(report.assertions[0].name, "status page overall")
        self.assertEqual(report.assertions[1].name, "attached script executable")
        self.assertEqual(report.assertions[1].status, "failed")

    def test_guest_debug_units_cover_expected_services(self) -> None:
        containers = {item.key: item for item in build_containers(940)}

        self.assertEqual(
            guest_debug_units(containers["baseline"]),
            (
                "proxnix-apply-config.service",
                "proxnix-baseline-attached.service",
                "proxnix-secret-oneshot-proxnix-common-admin-password.service",
                "proxnix-secret-template-baseline-report.service",
                "proxnix-secret-oneshot-baseline-oneshot.service",
                "proxnix-exercise-service-reader.service",
                "proxnix-exercise-podman.service",
                "nginx.service",
                "proxnix-exercise-baseline-status.service",
            ),
        )

    def test_cleanup_command_stops_destroys_and_removes_dirs(self) -> None:
        command = build_existing_container_cleanup_command("940")
        self.assertIn('pct status 940', command)
        self.assertIn('pct stop 940', command)
        self.assertIn('pct unmount 940', command)
        self.assertIn('pct unlock 940', command)
        self.assertIn('pct destroy 940', command)
        self.assertIn('rm -rf /var/lib/proxnix/containers/940 /var/lib/proxnix/private/containers/940', command)

    def test_existing_exercise_container_requires_cleanup_flag(self) -> None:
        container = ExerciseContainer(
            key="baseline",
            vmid="940",
            hostname="proxnix-exercise-baseline",
            memory_mb=3072,
            swap_mb=1024,
            cores=2,
            disk_gb=8,
        )
        report = RunReport(started_at="2026-04-18T00:00:00+00:00")
        session = _FakeSession()
        responses = [
            CompletedProcess(
                args=["ssh"],
                returncode=0,
                stdout="hostname: proxnix-exercise-baseline\n",
                stderr="",
            )
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("proxnix_workstation.exercise_cli.run_logged_remote_command", side_effect=responses):
                with self.assertRaises(ProxnixWorkstationError) as ctx:
                    ensure_container_slots_available(
                        report,
                        Path(temp_dir),
                        session,
                        [container],
                        cleanup_existing=False,
                    )

        self.assertIn("--cleanup-existing", str(ctx.exception))

    def test_cleanup_existing_exercise_container_runs_cleanup_command(self) -> None:
        container = ExerciseContainer(
            key="baseline",
            vmid="940",
            hostname="proxnix-exercise-baseline",
            memory_mb=3072,
            swap_mb=1024,
            cores=2,
            disk_gb=8,
        )
        report = RunReport(started_at="2026-04-18T00:00:00+00:00")
        session = _FakeSession()
        calls: list[tuple[str, str]] = []

        def fake_run_logged_remote_command(report, artifacts_dir, session, label, remote_command, *, check=True):
            calls.append((label, remote_command))
            if label == "inspect-existing-940":
                return CompletedProcess(
                    args=["ssh"],
                    returncode=0,
                    stdout="hostname: proxnix-exercise-baseline\n",
                    stderr="",
                )
            if label == "cleanup-existing-940":
                return CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected call: {label}")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("proxnix_workstation.exercise_cli.run_logged_remote_command", side_effect=fake_run_logged_remote_command):
                ensure_container_slots_available(
                    report,
                    Path(temp_dir),
                    session,
                    [container],
                    cleanup_existing=True,
                )

        self.assertEqual([label for label, _ in calls], ["inspect-existing-940", "cleanup-existing-940"])
        self.assertIn("pct destroy 940", calls[1][1])

    def test_cleanup_refuses_non_exercise_container(self) -> None:
        container = ExerciseContainer(
            key="baseline",
            vmid="940",
            hostname="proxnix-exercise-baseline",
            memory_mb=3072,
            swap_mb=1024,
            cores=2,
            disk_gb=8,
        )
        report = RunReport(started_at="2026-04-18T00:00:00+00:00")
        session = _FakeSession()
        responses = [
            CompletedProcess(args=["ssh"], returncode=0, stdout="hostname: real-container\n", stderr="")
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("proxnix_workstation.exercise_cli.run_logged_remote_command", side_effect=responses):
                with self.assertRaises(ProxnixWorkstationError) as ctx:
                    ensure_container_slots_available(
                        report,
                        Path(temp_dir),
                        session,
                        [container],
                        cleanup_existing=True,
                    )

        self.assertIn("refusing to destroy a non-exercise container", str(ctx.exception))

    def test_wait_for_apply_retries_missing_pct_config_error(self) -> None:
        session = _FakeSession()
        responses = [
            CompletedProcess(
                args=["ssh"],
                returncode=2,
                stdout="",
                stderr="Configuration file 'nodes/elitelab/lxc/950.conf' does not exist\n",
            ),
            CompletedProcess(
                args=["ssh"],
                returncode=0,
                stdout="ready\n",
                stderr="",
            ),
        ]

        with patch("proxnix_workstation.exercise_cli.pct_exec_shell", side_effect=responses) as pct_exec_shell_mock:
            with patch("proxnix_workstation.exercise_cli.time.sleep", return_value=None) as sleep_mock:
                wait_for_apply(session, "950", 30)

        self.assertEqual(pct_exec_shell_mock.call_count, 2)
        sleep_mock.assert_called_once_with(10)

    def test_wait_for_apply_raises_when_guest_reports_failed(self) -> None:
        session = _FakeSession()
        response = CompletedProcess(
            args=["ssh"],
            returncode=2,
            stdout="failed\n",
            stderr="guest apply failed\n",
        )

        with patch("proxnix_workstation.exercise_cli.pct_exec_shell", return_value=response):
            with self.assertRaises(ProxnixWorkstationError) as ctx:
                wait_for_apply(session, "950", 30)

        self.assertIn("wait-for-apply failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
