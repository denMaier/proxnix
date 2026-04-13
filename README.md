# proxnix

Host-managed NixOS LXC containers for Proxmox.

proxnix turns Proxmox container metadata, optional host-side YAML, and optional Quadlet files into a staged NixOS configuration that is copied into the guest at boot. The guest then applies that configuration only when the managed config hash changes.

This repository is the install/bootstrap layer. It owns the shared hooks,
helpers, and baseline Nix modules. Site-specific data is meant to live
separately under `/etc/pve/proxnix/site.nix`, `/etc/pve/proxnix/containers/`,
and `/etc/pve/priv/proxnix/`.

## What you get

- Proxmox-first networking and SSH key management
- Host-rendered NixOS config for each container
- Native NixOS services or Podman Quadlet workloads
- SOPS + age secrets that work for both native services and containers
- Shared admin user with password-hash-from-secret support
- Small operational helpers for health checks, bootstrap, and secrets

## Documentation

Full documentation lives under [`docs/`](docs/index.md). Start there.

If you use MkDocs, `mkdocs.yml` is included so the docs render as a small documentation site.

## Quick start

See the full [installation guide](docs/getting-started/installation.md) and [first container walkthrough](docs/getting-started/first-container.md) for the complete setup path, including secrets, admin user passwords, and workstation configuration.
