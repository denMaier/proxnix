# proxnix

Host-managed NixOS LXC containers for Proxmox.

proxnix turns Proxmox container metadata and host-side Nix modules into a staged NixOS configuration that is copied into the guest at boot. The guest then applies that configuration only when the managed config hash changes.

This repository is the install/bootstrap layer. It owns the shared hooks,
helpers, and baseline Nix modules. Site-specific data is meant to live in a
separate workstation-owned site repo and gets published into `/var/lib/proxnix/`
on each Proxmox node as a relay cache.

For host installs, the preferred distribution path is the helper-script style
entrypoint under `host/remote/`, which installs the published `proxnix-host`
Debian package for you. The `.deb` remains the underlying package-managed
upgrade and uninstall path.

## Repo layout

- `host/` contains the Proxmox-host install/runtime code: hooks, installers, inventory, helper scripts, and the shared managed Nix modules.
- `workstation/` contains the workstation-authoritative CLI, TUI, Electrobun manager app, Nix flake, packaging scripts, and workstation module exports.
- `docs/` contains shared human-facing documentation.

## What you get

- Proxmox-first networking and SSH key management
- Host-rendered NixOS config for each container
- Native NixOS services and Nix-authored container workloads
- SOPS + age secrets that work for both native services and containers
- Shared admin user with password-hash-from-secret support
- Small operational helpers for health checks, publishing, and secrets

## Documentation

Full documentation lives under [`docs/`](docs/index.md). Start there.

If you use MkDocs, `mkdocs.yml` is included so the docs render as a small documentation site.

## Quick start

See the full [installation guide](docs/getting-started/installation.md) and [first container walkthrough](docs/getting-started/first-container.md) for the complete setup path, including secrets, admin user passwords, and workstation configuration.
