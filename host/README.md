# Proxnix Host

This tree owns the Proxmox host install and runtime layer.

```text
host/
  install.sh          Stable local installer entrypoint
  uninstall.sh        Stable local uninstall entrypoint
  install/            Installer implementations copied to hosts
  runtime/            Source payload installed onto Proxmox nodes
  deploy/             Ansible playbooks and example inventory
  remote/             Curl-friendly remote installer entrypoints
  packaging/          Debian package build scripts and maintainer scripts
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

Keep public entrypoints at `host/install.sh`, `host/uninstall.sh`, and
`host/remote/*.sh` stable. Move implementation details under the grouped
directories instead.
