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
        poststop = (
            ROOT / "host" / "runtime" / "lxc" / "hooks" / "nixos-proxnix-poststop"
        ).read_text(encoding="utf-8")

        self.assertIn("nix --version", playbook)
        self.assertNotIn("become: true", playbook)
        self.assertIn("proxnix_nix_install_mode: require-existing", playbook)
        self.assertIn("proxnix_nix_install_mode in ['require-existing', 'determinate']", playbook)
        self.assertIn("https://install.determinate.systems/nix", playbook)
        self.assertIn("sh -s -- install --no-confirm", playbook)
        self.assertIn("creates: /nix/nix-installer", playbook)
        self.assertIn("proxnix_nix_install_mode == 'determinate'", playbook)
        self.assertNotIn("ansible.builtin.apt", playbook)
        self.assertIn("proxnix_install_source_dir: \"{{ proxnix_data_dir }}/install-source\"", playbook)
        self.assertIn("proxnix_host_profile: /nix/var/nix/profiles/proxnix-host", playbook)
        self.assertIn('proxnix_host_flake_ref: "{{ proxnix_install_source_dir }}#proxnix-host"', playbook)
        self.assertIn("host/runtime", playbook)
        self.assertIn("host/install", playbook)
        self.assertIn("host/nix", playbook)
        self.assertIn("nix profile install --no-write-lock-file --profile", playbook)
        self.assertIn("proxnix-host-activate", playbook)
        self.assertIn("Verify proxnix host tools are installed", playbook)
        self.assertIn("path: /etc/nix/nix.conf", playbook)
        self.assertIn("experimental-features = nix-command flakes", playbook)
        self.assertIn("keep-outputs = true", playbook)
        self.assertIn("keep-derivations = true", playbook)
        self.assertIn("install Nix first or rerun with -e proxnix_nix_install_mode=determinate", playbook)
        self.assertIn('nix --extra-experimental-features "nix-command flakes" eval --expr true', playbook)
        self.assertIn("proxnix-reconcile", playbook)

        self.assertIn('PROXNIX_PRESTART_GOLDEN_BUILD:-1', prestart)
        self.assertIn("set -euo pipefail", prestart)
        self.assertIn('if ! proxnix_validate_vmid "$VMID"', prestart)
        self.assertIn("STAGE_COMPLETE=1", prestart)
        self.assertIn('proxnix-reconcile-build-golden', prestart)
        self.assertIn('PROXNIX_PRESTART_BUILD:-1', prestart)
        self.assertIn('proxnix-reconcile-build --vmid "$VMID"', prestart)
        self.assertIn('proxnix-host pve-conf-to-nix', prestart)
        self.assertNotIn('pve-conf-to-nix.py', prestart)
        self.assertNotIn('copy/etc/nixos/configuration.nix', prestart)
        self.assertNotIn("PROXNIX_PRESTART_RECONCILE", prestart)
        self.assertNotIn('systemctl start --no-block "proxnix-reconcile@${VMID}.service"', prestart)
        self.assertIn('proxnix-reconcile-seed-offline --vmid "$VMID" --rootfs "$ROOTFS"', mount)
        self.assertIn("set -euo pipefail", mount)
        self.assertIn('if ! proxnix_validate_vmid "$VMID"', mount)
        self.assertIn('"$PROXNIX_HOST_BIN" reconcile podman-secrets', mount)
        self.assertNotIn("proxnix_reconcile_podman_secrets.py", mount)
        self.assertNotIn("PYEOF", mount)
        self.assertIn("rsync -a --delete", mount)
        self.assertIn("/var/lib/proxnix/build-input", mount)
        self.assertNotIn('copy_guest_file "${COPY_ETC_NIXOS_DIR}/configuration.nix"', mount)
        self.assertNotIn('bind_ro_dir "${BIND_CONFIG_DIR}" "${PROXNIX_CONFIG_DIR}"', mount)
        self.assertIn("set -euo pipefail", poststop)
        self.assertIn('if ! proxnix_validate_vmid "$VMID"', poststop)
        common = (
            ROOT / "host" / "runtime" / "lxc" / "hooks" / "nixos-proxnix-common.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("proxnix_validate_vmid()", common)
        self.assertNotIn("proxnix-host-identity.XXXXXX.yaml", common)

    def test_flake_packages_host_runtime(self) -> None:
        flake = (ROOT / "flake.nix").read_text(encoding="utf-8")
        package = (ROOT / "host" / "nix" / "proxnix-host.nix").read_text(
            encoding="utf-8"
        )
        activate = (
            ROOT / "host" / "runtime" / "bin" / "proxnix-host-activate"
        ).read_text(encoding="utf-8")

        self.assertIn("proxnix-host = pkgs.callPackage ./host/nix/proxnix-host.nix", flake)
        self.assertIn("nixpkgs.url = \"github:NixOS/nixpkgs/nixos-unstable\"", flake)
        self.assertIn("cp -R host/runtime/bin/.", package)
        self.assertIn("proxnix-host-uninstall", package)
        self.assertIn("ln -s proxnix-host-uninstall", package)
        self.assertNotIn("pve-conf-to-nix.py", package)
        self.assertIn("proxnix_authority_render.py", package)
        self.assertNotIn("proxnix_reconciler_state.py", package)
        self.assertNotIn("proxnix_reconcile_podman_secrets.py", package)
        self.assertIn("${age}/bin/age", package)
        self.assertIn("${jq}/bin/jq", package)
        self.assertIn("${rsync}/bin/rsync", package)
        self.assertIn("${sops}/bin/sops", package)
        self.assertIn("/nix/var/nix/profiles/proxnix-host", activate)
        self.assertIn("systemctl enable --now proxnix-gc.timer", activate)
        self.assertNotIn("systemctl disable --now proxnix-reconcile.timer", activate)
        self.assertNotIn('cp host/runtime/bin/proxnix-host-activate "$out/bin/proxnix-host-activate"', package)
        self.assertIn("proxnix-authority-render", activate)
        self.assertIn("proxnix-gc", activate)
        self.assertIn("proxnix-host", activate)
        self.assertIn("proxnix-reconcile-build-golden", activate)
        self.assertIn("proxnix-reconcile-build", activate)
        self.assertIn("proxnix-reconcile-seed", activate)
        self.assertIn("proxnix-reconcile-seed-offline", activate)
        self.assertIn("proxnix-reconcile-activate", activate)
        self.assertIn("proxnix-reconciler-state", activate)
        self.assertIn("proxnix-host-uninstall", activate)
        create_lxc = (
            ROOT / "host" / "runtime" / "bin" / "proxnix-create-lxc"
        ).read_text(encoding="utf-8")
        self.assertIn("CT_HOSTNAME=", create_lxc)
        self.assertNotIn("\nHOSTNAME=", create_lxc)
        doctor = (ROOT / "host" / "runtime" / "bin" / "proxnix-doctor").read_text(
            encoding="utf-8"
        )
        state_wrapper = (
            ROOT / "host" / "runtime" / "bin" / "proxnix-reconciler-state"
        ).read_text(encoding="utf-8")
        self.assertNotIn("proxnix_reconciler_state.py", doctor)
        self.assertNotIn("proxnix_reconcile_podman_secrets.py", doctor)
        self.assertIn('rm -f "$PROXNIX_LIB_DIR/proxnix_reconciler_state.py"', activate)
        self.assertIn('"$PROXNIX_HOST_BIN" state "$@"', state_wrapper)
        self.assertIn('rm -f "$PROXNIX_LIB_DIR/proxnix_reconcile_podman_secrets.py"', activate)

    def test_uninstall_removes_proxnix_host_profile(self) -> None:
        uninstall = (ROOT / "host" / "install" / "uninstall.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("/usr/local/bin", uninstall)
        self.assertIn("/nix/var/nix/profiles/proxnix-host", uninstall)
        self.assertIn("/nix/var/nix/profiles/proxnix-host-tools", uninstall)
        self.assertIn('do_rm "$PROXNIX_LOCAL_BIN_DIR/age"', uninstall)
        self.assertIn('do_rm "$PROXNIX_LOCAL_BIN_DIR/jq"', uninstall)
        self.assertIn('do_rm "$PROXNIX_LOCAL_BIN_DIR/rsync"', uninstall)
        self.assertIn('do_rm "$PROXNIX_LOCAL_BIN_DIR/sops"', uninstall)
        self.assertIn('do_rm "$PROXNIX_SBIN_DIR/proxnix-host"', uninstall)
        self.assertIn('do_rm "$PROXNIX_SBIN_DIR/proxnix-host-activate"', uninstall)
        self.assertIn('do_rm "$PROXNIX_SBIN_DIR/proxnix-host-uninstall"', uninstall)
        self.assertIn('[[ ! -e "$path" && ! -L "$path" ]]', uninstall)
        self.assertIn('"$PROXNIX_HOST_PROFILE"-*-link', uninstall)
        self.assertIn('"$PROXNIX_LEGACY_HOST_TOOLS_PROFILE"-*-link', uninstall)
        self.assertIn('PROXNIX_DEPLOY_GCROOT_DIR="${PROXNIX_DATA_DIR}/gcroots/deploy"', uninstall)
        self.assertIn("remove_deploy_gcroots", uninstall)

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
