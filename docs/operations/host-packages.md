# Host Deployment

Host deployment is intentionally single-path: use Ansible.

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml
```

The playbook verifies that the target is a Proxmox host, enables
`nix-command flakes`, installs proxnix host tools through
`/nix/var/nix/profiles/proxnix-host-tools`, exposes them through
`/usr/local/bin`, and then installs the proxnix LXC hooks, host helper commands,
shared Nix modules, and systemd units. By default Nix must already be installed.
To opt into installing Nix with the Determinate Systems installer, run:

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml -e proxnix_nix_install_mode=determinate
```

To update proxnix host files, rerun the same playbook. To remove the installed
runtime while keeping relay data under `/var/lib/proxnix`, run:

```bash
proxnix-uninstall
```

This removes the proxnix host tool symlinks and profile, but it does not remove
Nix. If Nix was installed through the Determinate installer mode, remove it
separately with `/nix/nix-installer uninstall`.
