# Architecture

proxnix is built around a strict host-render / guest-apply split.

## The core idea

The Proxmox host always decides what the guest should look like before the guest starts running. The guest only receives already-rendered state and applies it when necessary.

That gives proxnix these properties:

- the host remains the control plane
- the guest remains a normal NixOS system
- container startup can stage config, secrets, systemd units, and helper scripts in one place
- repeated starts are cheap because the guest compares hashes before rebuilding

## Lifecycle

```
  pct start <vmid>
       │
       ▼
  ┌─────────────────────────────────────────────┐
  │  1. pre-start hook (host)                    │
  │     Read PVE conf + Nix drop-ins             │
  │     Run pve-conf-to-nix.py                   │
  │     Stage secrets, scripts                    │
  │     Compute diagnostic config hash            │
  │     Output: /run/proxnix/<vmid>/              │
  └──────────────────────┬──────────────────────┘
                         │
                         ▼
  ┌─────────────────────────────────────────────┐
  │  2. mount hook (host, writes to guest rootfs)│
  │     Copy /etc/nixos/configuration.nix         │
  │     Bind /var/lib/proxnix/config/managed/     │
  │     Copy root-only secrets into /var/lib/proxnix/secrets/ │
  │     Remove legacy guest rebuild service       │
  │     Reconcile Podman secrets.json             │
  └──────────────────────┬──────────────────────┘
                         │
                         ▼
  ┌─────────────────────────────────────────────┐
  │  3. host reconciler                          │
  │     Evaluate authority manifest              │
  │     Build desired NixOS closure              │
  │     Seed closure into CT                     │
  │     Activate exact system path               │
  │     Verify /run/current-system               │
  └─────────────────────────────────────────────┘
```

### 1. Proxmox starts a NixOS CT

When `ostype=nixos`, Proxmox auto-includes the proxnix LXC config snippets. Those register two hooks:

- `nixos-proxnix-prestart`
- `nixos-proxnix-mount`

Local harnesses should invoke those same scripts directly rather than
reimplementing their behavior. `nixos-proxnix-prestart` accepts
`--vmid/--pve-conf`, and `nixos-proxnix-mount` accepts `--vmid/--rootfs`, so a
test VM can drive the exact render/apply path without drifting from production.

### 2. Pre-start hook renders desired state

The pre-start hook runs on the Proxmox host before the container rootfs is handed off.

It builds a stage directory at:

```text
/run/proxnix/<vmid>/
```

Important stage subtrees:

| Path | Contents |
|------|----------|
| `bind/config/{configuration.nix,managed/...}` | Desired NixOS config tree hashed by the host |
| `bind/runtime/{current-config-hash,vmid}` | Diagnostic markers |
| `bind/secrets/` | Compiled encrypted per-container runtime store plus staged identity |
| `copy/runtime/bin/` | Copied helper scripts from host `dropins/*.{sh,py}` plus `proxnix-secrets` |
| `copy/etc/nixos/configuration.nix` | Copied guest entrypoint |

The pre-start hook copies the node-local managed Nix files, runs
`pve-conf-to-nix.py`, pulls in host-side drop-ins, stages the compiled
per-container runtime store plus the container identity, and computes a hash of
the rendered managed tree.

### 3. Mount hook syncs the stage into the guest rootfs

The mount hook is the only proxnix hook that writes into the guest filesystem.
`/etc/nixos/configuration.nix` and guest runtime helpers are copied into place.
The managed Nix tree under
`/var/lib/proxnix/config/managed/` plus diagnostic runtime markers under
`/var/lib/proxnix/runtime/` are wired in from the stage read-only. Secret files
are copied into the guest as root-owned regular files.

It exposes the staged assets into places such as:

| Stage source | Guest destination |
|-------------|-------------------|
| `copy/etc/nixos/configuration.nix` | `/etc/nixos/configuration.nix` |
| `bind/config/managed/` | `/var/lib/proxnix/config/managed/` |
| `bind/runtime/current-config-hash` | `/var/lib/proxnix/runtime/current-config-hash` |
| `bind/runtime/vmid` | `/var/lib/proxnix/runtime/vmid` |
| `copy/runtime/bin/*` | `/var/lib/proxnix/runtime/bin/` |
| `bind/secrets/*` | `/var/lib/proxnix/secrets/` |

It also removes legacy `proxnix-apply-config` service files if they are present.

### 4. Host reconciler activates the desired system

`proxnix-reconcile` runs on the Proxmox node. It renders the authority wrapper,
evaluates cluster-level `proxnix.containers` through the node view at
`proxnix.nodes.<node>`, skips CTs that are not local according to `pct status`,
builds the selected local NixOS system closure, imports that closure into the
CT, runs the target system's
`switch-to-configuration`, verifies `/run/current-system`, and writes status
under `/var/lib/proxnix/status/`.

`current-config-hash` may still appear as diagnostic metadata, but it is not the
activation source of truth.

## Persistent state and experimentation

While `/etc/nixos/configuration.nix` and `/var/lib/proxnix/config/managed/`
are host-managed and refreshed on restart, proxnix **does not touch other
parts of the guest rootfs**.

- **`/var/lib/`**: Databases and application data stay persistent.
- **`/etc/nixos/local.nix`**: This is an unmanaged sandbox for debugging. Normal proxnix convergence does not evaluate config inside the guest.
- **Experimental changes**: Commit final configuration to the workstation-owned site repo, publish it, then run `proxnix deploy` or `proxnix-reconcile` on the host.

## What `configuration.nix` imports

The guest entrypoint is intentionally small. It imports:

- `base.nix` — install-layer guest baseline: LXC adjustments, age setup, login summary
- `common.nix` — proxnix option module for the shared operator baseline
- `security-policy.nix` — host-enforced security posture that guest-local overrides should not relax
- optional `site.nix` — site-wide overrides, typically managed from a separate repo
- `proxmox.nix` — generated from PVE conf (hostname, DNS, SSH keys)
- every top-level managed drop-in `*.nix`
- optional `/etc/nixos/local.nix`

That last file is the escape hatch for guest-only experimentation. proxnix does not manage it.

`base.nix`, `common.nix`, and `security-policy.nix` are separate on purpose:

- `common.nix` defines the reusable `proxnix.common.*` options and applies them
- `security-policy.nix` is the trust-boundary layer that forcefully keeps host-managed security settings in place
- `base.nix` is the install repo's default runtime baseline and convenience layer

That keeps the baseline policy amendable. A separate site repo can add a `site.nix`
that changes `proxnix.common.*` without needing to fork the install-layer files.

## The admin user

`common.nix` and `security-policy.nix` create a shared operator account (default: `admin`, UID 1000) on every proxnix-managed container. Key behaviors:

- **SSH keys:** By default, inherits the same authorized keys as `root` (which come from the Proxmox CT config)
- **Password:** Locked by default. Set via the `common_admin_password_hash` shared secret (see [installation](../getting-started/installation.md#step-4-set-the-admin-user-password-hash))
- **sudo:** Member of `wheel`. By default, `wheelNeedsPassword = true`, so the admin password hash secret must be set for `sudo` to work
- **Root:** Root password is locked, SSH root login is key-only (`prohibit-password`)

These defaults are exposed via `proxnix.common.*` options in `common.nix`, while the enforced security posture lives in `security-policy.nix`.
For additive package changes, prefer `proxnix.common.extraPackages` from `site.nix`.

## Podman enablement rule

proxnix does not manage Podman enablement. If a guest uses Podman, that comes
from guest Nix config, typically through a shared import in `site.nix` and a
per-container `dropins/*.nix` activation module.

## Attached systemd units and scripts

Host-side `dropins/` files are routed by extension:

| Extension | Destination |
|-----------|-------------|
| `*.nix` | Guest managed Nix imports |
| `*.service` | Rejected; move service definitions into `dropins/*.nix` so they stay guest Nix-managed |
| `*.sh`, `*.py` | `/var/lib/proxnix/runtime/bin/` on `PATH` |
| `*.container`, `*.volume`, `*.network`, `*.pod`, `*.image`, `*.build` | Rejected; raw host-side Quadlet staging is no longer supported |

This lets you augment a container without editing the shared baseline.
