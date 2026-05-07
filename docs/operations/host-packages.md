# Host Deployment

Host deployment is intentionally single-path: use Ansible.

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml
```

The playbook verifies that the target is a Proxmox host, checks whether Nix is
installed, enables `nix-command flakes`, installs or upgrades
`/nix/var/nix/profiles/proxnix-host`, and runs `proxnix-host-activate`.
Activation links the LXC config snippets, host helper commands, host tools,
shared Nix modules, and systemd units into the host filesystem.

By default the playbook requires Nix to already exist on the target host. Pass
`-e proxnix_nix_install_mode=determinate` when you want the playbook to
bootstrap Nix with the Determinate Systems installer as a convenience option.

The installer builds the requested host package, switches the Nix profile, and
then activates the profile idempotently. Activation overwrites proxnix-managed
links in place. It preserves deployment GC roots under
`/var/lib/proxnix/gcroots/deploy`; prune them with `proxnix-host gc` instead of
the installer. It also preserves relay data and host secrets under
`/var/lib/proxnix/authority`, `/var/lib/proxnix/containers`,
`/var/lib/proxnix/private`, and `/etc/proxnix`. Use
`-e proxnix_install_clean_slate=true` only as an explicit repair/reset path for
stale pre-Nix or broken installs.

By default, production installs build `github:denMaier/proxnix#proxnix-host`.
Override `proxnix_host_flake_ref` to pin a release or branch. For development
deploys of the current checkout, use `host/deploy/ansible/install-local.yml`;
it stages `/var/lib/proxnix/install-source` and then runs the same installer
against that local flake ref. The development path resets that source directory
before staging and preserves the freshly staged source after activation.

To update proxnix host files, rerun the same playbook. To remove the installed
runtime while keeping relay data under `/var/lib/proxnix`, run:

```bash
proxnix-host-uninstall
```

The uninstall command removes the proxnix host symlinks and profile, but it does
not remove Nix. If Nix was installed through the Determinate installer mode,
remove it separately with `/nix/nix-installer uninstall`.
