# Proxnix Host

This tree owns the Proxmox host install and runtime layer.

```text
host/
  install/            Uninstall implementation copied to hosts
  runtime/            Source payload installed onto Proxmox nodes
  deploy/             Ansible playbooks and example inventory
  extras/             Optional host system units and udev rules
```

`host/runtime/` mirrors the installed runtime by role:

```text
runtime/
  lxc/config/         LXC config snippets
  lib/                Host-side helper scripts installed under /usr/local/lib/proxnix
  bin/                Host admin commands installed under /usr/local/sbin
  nix/                Shared managed NixOS modules installed under /var/lib/proxnix
  systemd/            Proxnix-owned systemd units installed under /etc/systemd/system
```

The Rust host controller lives in `../crates/proxnix-host`. The host package
installs it as `/usr/local/sbin/proxnix-host`; systemd units dispatch directly
into that binary. Proxnix registers one narrow LXC `start-host` hook for
pre-init payload refresh and idempotent closure copy.

Install host runtime files with `host/deploy/ansible/install.yml`. The
uninstall implementation in `host/install/uninstall.sh` is installed as
`proxnix-host-uninstall`.
