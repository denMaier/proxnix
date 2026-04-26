# Proxnix Host

This tree owns the Proxmox host install and runtime layer.

```text
host/
  uninstall.sh        Stable local uninstall entrypoint
  install/            Uninstall implementation copied to hosts
  runtime/            Source payload installed onto Proxmox nodes
  deploy/             Ansible playbooks and example inventory
  extras/             Optional host system units and udev rules
```

`host/runtime/` mirrors the installed runtime by role:

```text
runtime/
  lxc/config/         LXC config snippets
  lxc/hooks/          Proxmox/LXC lifecycle hooks
  lib/                Host-side helper scripts installed under /usr/local/lib/proxnix
  bin/                Host admin commands installed under /usr/local/sbin
  nix/                Shared managed NixOS modules installed under /var/lib/proxnix
  systemd/            Proxnix-owned systemd units installed under /etc/systemd/system
```

Install host runtime files with `host/deploy/ansible/install.yml`. Keep
`host/uninstall.sh` stable as the repo-local source for the `proxnix-uninstall`
helper installed by Ansible.
