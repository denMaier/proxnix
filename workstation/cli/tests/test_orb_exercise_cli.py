from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from proxnix_workstation.local_nixos_container_backend import (
    render_nspawn_backend_shell,
    render_prestart_stage_apply_shell,
)
from proxnix_workstation.orb_exercise_cli import (
    build_probe_container,
    build_remote_probe_script,
    render_bootstrap_config,
    render_fake_pve_conf,
)
from proxnix_workstation.test_site_fixture import render_test_site


class OrbExerciseCliTests(unittest.TestCase):
    def test_nspawn_backend_sanitizes_runtime_tree(self) -> None:
        rendered = render_nspawn_backend_shell()

        self.assertIn('systemd-nspawn --cleanup -D "${ROOTFS}" -M "${GUEST_MACHINE}"', rendered)
        self.assertIn('rm -rf "${ROOTFS}/dev" "${ROOTFS}/proc" "${ROOTFS}/run"', rendered)
        self.assertIn('mkdir -p "${ROOTFS}/boot" "${ROOTFS}/dev" "${ROOTFS}/proc" "${ROOTFS}/run"', rendered)

    def test_prestart_stage_apply_shell_materializes_guest_inputs(self) -> None:
        rendered = render_prestart_stage_apply_shell()

        self.assertIn('local_nixos_container_apply_prestart_stage', rendered)
        self.assertIn('cp -a "${bind_config_dir}/." "${proxnix_config_dir}/"', rendered)
        self.assertIn('install -m 0400 "${bind_runtime_dir}/current-config-hash"', rendered)
        self.assertIn('install -m 0644 "${copy_etc_systemd_attached_dir}/proxnix-apply-config.service"', rendered)
        self.assertIn('local_nixos_container_register_podman_secrets "${proxnix_secret_dir}" "$(cat "${bind_runtime_dir}/vmid")"', rendered)
        self.assertIn('"driverOptions": driver_opts,', rendered)
        self.assertIn('"metadata": {},', rendered)

    def test_render_test_site_includes_network_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_dir = Path(temp_dir) / "site"
            container = build_probe_container("9940")

            render_test_site(site_dir, vmid=container.vmid, token="orb-9940")

            dropin = (site_dir / "containers" / "9940" / "dropins" / "00-test-site.nix").read_text(
                encoding="utf-8"
            )
            secret_groups = (site_dir / "containers" / "9940" / "secret-groups.list").read_text(encoding="utf-8")
            helper_py = (site_dir / "containers" / "9940" / "dropins" / "proxnix-test-site-helper.py").read_text(
                encoding="utf-8"
            )

        self.assertIn("proxmoxLXC.manageNetwork = lib.mkForce true;", dropin)
        self.assertIn("services.openssh.ports = lib.mkForce [ 2222 ];", dropin)
        self.assertIn('virtualisation.containers.storage.settings.storage.driver = lib.mkForce "vfs";', dropin)
        self.assertIn('environment.etc."proxnix-orb-probe".text = "orb-probe-ok', dropin)
        self.assertIn("exercise-group", secret_groups)
        self.assertIn("podman-group", secret_groups)
        self.assertIn('print("helper-py-ok")', helper_py)

    def test_render_fake_pve_conf_includes_ssh_public_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conf_path = Path(temp_dir) / "ct.conf"
            container = build_probe_container("9940")

            render_fake_pve_conf(conf_path, container=container, root_public_key="ssh-ed25519 AAAA test@example")
            rendered = conf_path.read_text(encoding="utf-8")

        self.assertIn("hostname: proxnix-orb-probe", rendered)
        self.assertIn("unprivileged: 0", rendered)
        self.assertIn("ssh-public-keys: ssh-ed25519%20AAAA%20test%40example%0A", rendered)

    def test_render_bootstrap_config_includes_state_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "bootstrap.nix"
            render_bootstrap_config(config_path, state_version="25.11")
            rendered = config_path.read_text(encoding="utf-8")

        self.assertIn('boot.isContainer = true;', rendered)
        self.assertIn('nix.settings.sandbox = false;', rendered)
        self.assertIn('system.stateVersion = "25.11";', rendered)

    def test_remote_probe_script_materializes_root_channels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            container = build_probe_container("9940")

            rendered = build_remote_probe_script(
                repo_root=temp_path / "repo",
                relay_tree=temp_path / "relay",
                pve_conf_path=temp_path / "ct.conf",
                bootstrap_config_path=temp_path / "bootstrap.nix",
                container=container,
                timeout_seconds=60,
            )

        self.assertIn('rm -rf "${ROOT_CHANNELS}"', rendered)
        self.assertIn("if command -v chattr >/dev/null 2>&1; then", rendered)
        self.assertIn("find \"${ORB_EXERCISE_ROOT}\" -mindepth 2 -maxdepth 2 -type d -name 'run-*' -mmin +10 -exec chattr -R -i {} + >/dev/null 2>&1 || true", rendered)
        self.assertIn('chattr -R -i "${ORB_STATE_ROOT}" >/dev/null 2>&1 || true', rendered)
        self.assertIn('find "${ORB_EXERCISE_ROOT}" -mindepth 2 -maxdepth 2 -type d -name \'run-*\' -mmin +10 -exec rm -rf {} +', rendered)
        self.assertIn('cp -a --reflink=auto "${HOST_NIXPKGS}" "${ROOT_CHANNELS}/nixos"', rendered)
        self.assertIn('cp -a --reflink=auto "${HOST_NIXPKGS}" "${ROOT_CHANNELS}/nixpkgs-unstable"', rendered)
        self.assertIn('if ! nixos-install --root "${ROOTFS}" --system "${SYSTEM_PATH}" --no-root-password --no-bootloader >"${INSTALL_LOG}" 2>&1; then', rendered)
        self.assertIn('if ! chroot "${ROOTFS}" /nix/var/nix/profiles/system/activate >>"${INSTALL_LOG}" 2>&1; then', rendered)
        self.assertIn('show_install_log() {', rendered)
        self.assertIn('local_nixos_container_cleanup_mounts', rendered)
        self.assertIn('bash "${PRESTART_HOOK}" --vmid "${VMID}" --pve-conf "${PVE_CONF}"', rendered)
        self.assertIn('local_nixos_container_apply_prestart_stage "/run/proxnix/${VMID}"', rendered)
        self.assertNotIn('nixos-proxnix-mount', rendered)
        self.assertIn('local_nixos_container_prepare_runtime_tree', rendered)
        self.assertIn('local_nixos_container_reset_rootfs', rendered)
        self.assertIn('local_nixos_container_start', rendered)
        self.assertIn('cp -a "${RELAY_TREE}/private/." /var/lib/proxnix/private/', rendered)
        self.assertIn('if [ -n "$current" ] && [ "$current" = "$applied" ]; then', rendered)
        self.assertIn('systemctl start proxnix-test-site-status.service >/dev/null 2>&1 || true', rendered)
        self.assertIn('if [ -f /var/lib/proxnix-test-site/www/status.json ]; then', rendered)
        self.assertIn('proxnix-test-site-status.service', rendered)
        self.assertIn('printf "status-failed\\n"', rendered)
        self.assertIn('if systemctl is-failed --quiet "$unit"; then', rendered)
        self.assertIn('podman_state="$(systemctl show -p ActiveState --value proxnix-test-site-podman.service || true)"', rendered)
        self.assertIn('result_json="$(', rendered)
        self.assertIn('"status_file": status_file,', rendered)
        self.assertIn('"status_http_matches_file": status_http == status_file,', rendered)
        self.assertIn('local_nixos_container_stop', rendered)
        self.assertIn('local_nixos_container_clear_immutable', rendered)
        self.assertIn('rm -rf "/run/proxnix/${VMID}" "${RUN_ROOT}"', rendered)
        self.assertNotIn('printf "waiting\\n"\n              exit 1', rendered)
