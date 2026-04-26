# Host Deployment

Host deployment is intentionally single-path: use Ansible.

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml
```

The playbook verifies that the target is a Proxmox host, enables
`nix-command flakes`, stages the host flake source under
`/var/lib/proxnix/install-source`, installs or upgrades
`/nix/var/nix/profiles/proxnix-host`, and runs `proxnix-host-activate`.
Activation links the LXC hooks, host helper commands, host tools, shared Nix
modules, and systemd units into the host filesystem. By default Nix must already
be installed. To opt into installing Nix with the Determinate Systems installer,
run:

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml -e proxnix_nix_install_mode=determinate
```

To update proxnix host files, rerun the same playbook. To remove the installed
runtime while keeping relay data under `/var/lib/proxnix`, run:

```bash
proxnix-host-uninstall
```

`proxnix-uninstall` is kept as a compatibility alias. The uninstall command
removes the proxnix host symlinks and profile, but it does not remove Nix. If
Nix was installed through the Determinate installer mode, remove it separately
with `/nix/nix-installer uninstall`.
