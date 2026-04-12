# Architecture

proxnix is built around a strict host-render / guest-apply split.

## The core idea

The Proxmox host always decides what the guest should look like before the guest starts running. The guest only receives already-rendered state and applies it when necessary.

That gives proxnix these properties:

- the host remains the control plane
- the guest remains a normal NixOS system
- container startup can stage config, secrets, systemd units, and Quadlets in one place
- repeated starts are cheap because the guest compares hashes before rebuilding

## Lifecycle

```
  pct start <vmid>
       │
       ▼
  ┌─────────────────────────────────────────────┐
  │  1. pre-start hook (host)                    │
  │     Read PVE conf + YAML + dropins           │
  │     Run yaml-to-nix.py                       │
  │     Stage secrets, Quadlets, scripts          │
  │     Compute config hash                       │
  │     Output: /run/proxnix/<vmid>/              │
  └──────────────────────┬──────────────────────┘
                         │
                         ▼
  ┌─────────────────────────────────────────────┐
  │  2. mount hook (host, writes to guest rootfs)│
  │     Copy rendered config → /etc/nixos/        │
  │     Copy secrets → /etc/proxnix/secrets/      │
  │     Copy Quadlets → /etc/containers/systemd/  │
  │     Install proxnix-apply-config service      │
  │     Reconcile Podman secrets.json             │
  └──────────────────────┬──────────────────────┘
                         │
                         ▼
  ┌─────────────────────────────────────────────┐
  │  3. guest boot                               │
  │     proxnix-apply-config.service runs        │
  │     Compare current-config-hash              │
  │           vs applied-config-hash             │
  │     Same → exit (no rebuild)                 │
  │     Different → nixos-rebuild switch          │
  │     No channel → prompt for bootstrap         │
  └─────────────────────────────────────────────┘
```

### 1. Proxmox starts a NixOS CT

When `ostype=nixos`, Proxmox auto-includes the proxnix LXC config snippets. Those register two hooks:

- `nixos-proxnix-prestart`
- `nixos-proxnix-mount`

### 2. Pre-start hook renders desired state

The pre-start hook runs on the Proxmox host before the container rootfs is handed off.

It builds a stage directory at:

```text
/run/proxnix/<vmid>/
```

Important stage subtrees:

| Path | Contents |
|------|----------|
| `rendered/configuration.nix` | NixOS entrypoint |
| `rendered/managed/{base,common,proxmox,user}.nix` | Core managed modules |
| `rendered/managed/dropins/` | Extra Nix modules from host `dropins/` |
| `runtime/systemd/` | Attached systemd units from host `dropins/*.service` |
| `runtime/bin/` | Scripts from host `dropins/*.{sh,py}` |
| `quadlet/` | Quadlet unit files and app config |
| `secrets/` | Encrypted SOPS YAML stores |
| `keys/` | Shared age identity (if configured) |
| `meta/` | Config hash, VMID, bootstrap marker |

The pre-start hook copies the shared Nix files, runs `yaml-to-nix.py`, pulls in host-side drop-ins, stages encrypted secret stores, and computes a hash of the rendered managed tree.

### 3. Mount hook syncs the stage into the guest rootfs

The mount hook is the only proxnix hook that writes into the guest filesystem.

It copies the staged assets into places such as:

| Stage source | Guest destination |
|-------------|-------------------|
| `rendered/configuration.nix` | `/etc/nixos/configuration.nix` |
| `rendered/managed/` | `/etc/nixos/managed/` |
| `runtime/systemd/*.service` | `/etc/systemd/system.attached/` |
| `runtime/bin/*.{sh,py}` | `/usr/local/bin/` |
| `quadlet/*.container` etc. | `/etc/containers/systemd/` |
| `quadlet/` (full tree) | `/etc/proxnix/quadlets/` |
| `secrets/*.sops.yaml` | `/etc/proxnix/secrets/` |
| `keys/shared_identity.txt` | `/etc/age/shared_identity.txt` |

It also installs a generated `proxnix-apply-config` service and runner inside the guest.

### 4. Guest applies only changed config

Inside the guest, proxnix stores two hashes:

- current desired hash: `/etc/proxnix/current-config-hash`
- last applied hash: `/etc/proxnix/applied-config-hash`

At boot, the generated runner compares them.

- If the hash is unchanged, it exits immediately
- If the root nix channel is still missing, it asks you to run `/root/proxnix-bootstrap.sh`
- If the hash changed, it runs `nixos-rebuild switch` once for that boot and records the applied hash

## Persistent state and experimentation

While `/etc/nixos/managed/` is read-only and overwritten on restart, proxnix **does not touch other parts of the guest rootfs**.

- **`/var/lib/`**: Databases and application data stay persistent.
- **`/etc/nixos/local.nix`**: This is your sandbox. You can add config here and run `nixos-rebuild switch` inside the guest to test it.
- **Experimental changes**: You can iterate inside the guest before committing your final configuration to the Proxmox host. Once committed, a container restart will lock it in as the new host-managed source of truth.

## What `configuration.nix` imports

The guest entrypoint is intentionally small. It imports:

- `base.nix` — LXC adjustments, age setup, Podman config, login summary
- `common.nix` — admin user, SSH hardening, journald, packages
- `proxmox.nix` — generated from PVE conf (hostname, IP, DNS, SSH keys)
- `user.nix` — generated from `user.yaml` (native services)
- every managed drop-in `*.nix`
- optional `/etc/nixos/local.nix`

That last file is the escape hatch for guest-only experimentation. proxnix does not manage it.

## The admin user

`common.nix` creates a shared operator account (default: `admin`, UID 1000) on every proxnix-managed container. Key behaviors:

- **SSH keys:** By default, inherits the same authorized keys as `root` (which come from the Proxmox CT config)
- **Password:** Locked by default. Set via the `common_admin_password_hash` shared secret (see [installation](../getting-started/installation.md#step-4-set-the-admin-user-password-hash))
- **sudo:** Member of `wheel`. By default, `wheelNeedsPassword = true`, so the admin password hash secret must be set for `sudo` to work
- **Root:** Root password is locked, SSH root login is key-only (`prohibit-password`)

These defaults are configurable via `proxnix.common.*` options in `common.nix`.

## Podman enablement rule

proxnix enables Podman by default in the shared base config, but the pre-start hook writes a small Nix drop-in that disables Podman when no top-level Quadlet unit files are staged.

That keeps service-only containers lighter while still making container workloads easy to add.

## Attached systemd units and scripts

Host-side `dropins/` files are routed by extension:

| Extension | Destination |
|-----------|-------------|
| `*.nix` | Guest managed Nix imports |
| `*.service` | `/etc/systemd/system.attached/` |
| `*.sh`, `*.py` | `/usr/local/bin/` |
| `*.container`, `*.volume`, `*.network`, `*.pod`, `*.image`, `*.build` | `/etc/containers/systemd/` |

This lets you augment a container without editing the shared baseline.
