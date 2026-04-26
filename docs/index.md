<p align="center">
  <img src="assets/proxnix-icon.png" alt="Proxnix icon" width="128" height="128">
</p>

# Proxnix

Proxnix manages NixOS LXC containers from the Proxmox host.

> **Public alpha:** proxnix is pre-1.0 infrastructure software. The core
> workflows are usable, but the hosted Manager Web and NixOS deployment module
> are new and should be tested in a lab or non-critical environment first.

Instead of logging into each guest and hand-editing its production configuration, proxnix renders the desired guest system on the host, builds the NixOS closure on the Proxmox node, seeds that closure into the container, and activates the exact system path. The guest receives secrets, helper/file drop-ins, runtime markers, and a read-only debug snapshot of the host build input.

## Who this is for

proxnix is a good fit when you want:

- Proxmox to stay authoritative for container networking and basic container lifecycle
- NixOS to stay authoritative for the guest OS configuration
- one host-side place for per-container config and secrets
- a clean split between Proxmox-owned container metadata and guest-owned NixOS workloads

## Prerequisites

Before using proxnix you should be comfortable with:

- **Proxmox VE** — creating and managing LXC containers from the WebUI or CLI (`pct`)
- **NixOS basics** — what `nixos-rebuild switch` does, how NixOS modules and options work
- **SOPS and age** — the encryption tools proxnix uses for secrets ([age](https://github.com/FiloSottile/age), [SOPS](https://github.com/getsops/sops))
- **Container module basics** (if using container workloads) — for example `quadlet-nix` or another guest-side Nix module layer

You will need these tools installed on your **workstation** (the machine you manage secrets from):

- `ssh` — to reach the Proxmox host
- `ssh-keygen` — to generate SSH keys that `age` can use as recipients
- `sops` — to encrypt and decrypt secret stores
- `rsync` — to sync the relay cache to host nodes
- `python3` — used by the workstation CLI

## Mental model

There are four main layers:

1. **Proxmox metadata**: hostname, IP, gateway, DNS, search domain, SSH keys, CT features, rootfs, and lifecycle
2. **Host-side proxnix config**: install-layer Nix files plus optional site-wide `site.nix` and per-container `dropins/`
3. **Host authority and build**: the Proxmox node renders a local authority wrapper, evaluates the desired CT system, and builds the NixOS closure
4. **Seed and activation**: a stopped CT is seeded during the LXC mount hook and activates the exact system path on guest boot; a running CT can be seeded and activated explicitly with `proxnix-reconcile`

## The guest is not a black box

While the host is the *source of truth* for your declarative config, the guest is a **full NixOS system**.

- **Experimentation loop**: You can `pct enter <vmid>` for debugging and point a manual rebuild at `/var/lib/proxnix/build-input/configuration.nix`, but normal proxnix builds and seeding are owned by the Proxmox host.
- **Persistent state**: The build-input snapshot is refreshed on restart, but **guest-local state is persistent**. Databases in `/var/lib/`, the Nix store, and any unmanaged files like `/etc/nixos/local.nix` survive reboots.
- **Live debugging**: You can install temporary packages with `nix-shell -p` or check logs with `journalctl` just like on any other NixOS host.

```
┌─────────────────────────────────────────────────────────────────┐
│                     Proxmox host                                │
│                                                                 │
│  pct start <vmid>                                               │
│       │                                                         │
│       ▼                                                         │
│  ┌─────────────────────┐    ┌──────────────────────────────┐    │
│  │  pre-start hook      │───▶│  /run/proxnix/<vmid>/        │    │
│  │  render + build      │    │  rendered/ secrets/ runtime/ │    │
│  └─────────────────────┘    └──────────┬───────────────────┘    │
│                                         │                       │
│                                         ▼                       │
│                              ┌─────────────────────┐            │
│                              │  mount hook          │            │
│                              │  sync + seed rootfs  │            │
│                              └──────────┬──────────┘            │
│                                         │                       │
├─────────────────────────────────────────┼───────────────────────┤
│                     Guest (NixOS LXC)   │                       │
│                                         ▼                       │
│                              ┌─────────────────────┐            │
│                              │  boot activate unit  │            │
│                              │  switch next-system  │            │
│                              │  verify or revert    │            │
│                              └─────────────────────┘            │
└─────────────────────────────────────────────────────────────────┘
```

> **Important design decision:** proxnix stages build inputs at container startup only, not while the container is running. After changing any host-side file, restart the CT or run an explicit host reconcile for the change to take effect.

## Read this first

1. Start with [installation](getting-started/installation.md) if proxnix is not installed yet
2. Continue with [first container](getting-started/first-container.md) for the complete bootstrap path
3. Read [architecture](concepts/architecture.md) if you want the lifecycle and staging model
4. Read [configuration model](concepts/configuration-model.md) to understand which file owns which behavior
5. Read [secrets](concepts/secrets.md) before introducing credentials into workloads

## Repo split

Treat this repository as the **install repo**:

- `host/` owns the Proxmox install/runtime layer: hooks, helpers, installers, and the shared baseline Nix files
- `workstation/` owns the workstation-authoritative CLI, TUI, app, flake, and packaging files
- it may ship example workloads and example config shapes
- it does **not** need to own your live site data

In practice, live site data can be managed from a separate repo that writes:

- `/var/lib/proxnix/site.nix` for site-wide overrides
- `/var/lib/proxnix/containers/<vmid>/` for per-container config
- `/var/lib/proxnix/private/` for encrypted secrets

## Main workflows

### Native services

Use native services when the application already has a good NixOS module.

Typical inputs:

- `dropins/*.nix`
- optional secrets referenced from proxnix-managed paths

See [native services](workloads/native-services.md).

### Container workloads

Use container modules when the workload is container-first.

Typical inputs:

- `site.nix` for shared imports such as `quadlet-nix`
- `dropins/*.nix` for per-container activation
- secrets surfaced through proxnix secret helpers or the Podman shell driver from guest config

See [Quadlet workloads](workloads/quadlet-workloads.md).

## Day-2 operations

Common tasks:

- restart a CT after changing host-side proxnix files
- run `proxnix doctor` from the workstation or `proxnix-doctor` from the host
- manage workstation-side secrets with `proxnix secrets`
- run the host helper-script installer when bootstrapping a Proxmox node
- use the raw host `.deb` when you want manual or offline package control
- cut annotated `v*` release tags to publish host and workstation artifacts
- refresh the Homebrew tap formula and cask when releasing Proxnix Manager
- deploy Proxnix Manager Web behind a reverse auth proxy when you want shared browser access

See [day-2 operations](operations/day-2.md), [LXC exercise lab](operations/lxc-exercise-lab.md), [host packages](operations/host-packages.md), [Proxnix Manager](operations/proxnix-manager.md), [Proxnix Manager Web](operations/proxnix-manager-web.md), [releases](operations/releases.md), and [troubleshooting](operations/troubleshooting.md).
