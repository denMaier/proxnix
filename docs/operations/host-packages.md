# Host Deployment

Host deployment is intentionally single-path: use Ansible.

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml
```

The playbook verifies that the target is a Proxmox host with `sops`, Nix, and
flakes support available. It then installs the proxnix LXC hooks, host helper
commands, shared Nix modules, and systemd units.

To update proxnix host files, rerun the same playbook. To remove the installed
runtime while keeping relay data under `/var/lib/proxnix`, run:

```bash
proxnix-uninstall
```
