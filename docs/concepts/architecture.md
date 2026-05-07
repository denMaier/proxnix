# Architecture

proxnix is built around a strict host-build / guest-activate split.

## The core idea

The Proxmox host always decides what the guest should look like before the guest starts running. The guest receives an already-built system closure, activates that exact path, and keeps a copied build-input snapshot only for debugging.

That gives proxnix these properties:

- the host remains the control plane
- the guest remains a normal NixOS system
- reconciliation stages secrets, helper scripts, file drop-ins, and debug build inputs in one place
- repeated starts are cheap because the guest does not rebuild during normal convergence

## Lifecycle

```
  proxnix-host reconcile --vmid <vmid>
       │
       ▼
  ┌─────────────────────────────────────────────┐
  │  1. host reconcile                            │
  │     Read PVE conf + Nix drop-ins             │
  │     Run proxnix-host pve-conf-to-nix         │
  │     Stage secrets, scripts                    │
  │     Compute diagnostic config hash            │
  │     Build desired NixOS closure if needed     │
  │     Sync payload + system closure             │
  └──────────────────────┬──────────────────────┘
                         │
                         ▼
  ┌─────────────────────────────────────────────┐
  │  2. stopped or running CT sync                │
  │     Copy build-input debug snapshot           │
  │     Copy root-only secrets into /var/lib/proxnix/secrets/ │
  │     Seed desired closure into stopped rootfs   │
  │     Or seed through the running guest Nix daemon│
  └──────────────────────┬──────────────────────┘
                         │
                         ▼
  ┌─────────────────────────────────────────────┐
  │  3. guest boot activation                     │
  │     Read /var/lib/proxnix/runtime/next-system │
  │     Activate exact system path               │
  │     Verify /run/current-system               │
  │     Revert to previous-system on failure      │
  └─────────────────────────────────────────────┘
```

### 1. Proxmox starts a NixOS CT

When `ostype=nixos`, Proxmox auto-includes the proxnix LXC config snippets.
Those snippets set NixOS/LXC compatibility defaults and register one narrow
`start-host` hook. PVE still owns start/stop. Proxnix builds independently
through explicit host commands and the optional `nix-auto` timer; the hook only
refreshes cheap payload files and idempotently copies the already-built desired
closure into the mounted rootfs before init starts.

### 2. Reconciliation renders desired state

The reconciler renders a transient host-side stage directory at:

```text
/run/proxnix/<vmid>/
```

Important stage subtrees:

| Path | Contents |
|------|----------|
| `bind/config/{configuration.nix,managed/...}` | Desired NixOS config tree used by the host build and copied into the guest debug snapshot |
| `bind/runtime/{current-config-hash,vmid}` | Diagnostic markers |
| `bind/secrets/` | Compiled encrypted per-container runtime store plus staged identity |
| `copy/runtime/bin/` | Copied helper scripts from host `dropins/*.{sh,py}` plus `proxnix-secrets` |

The stage renderer copies the node-local managed Nix files, renders the
Proxmox CT config, pulls in host-side drop-ins, stages the compiled
per-container runtime store plus the container identity, computes a hash of the
rendered managed tree, and restricts the staged tree for the container's mapped
root UID. It does not copy Nix closures.

There is no LXC pre-start build hook. Build freshness is handled by running
explicit reconciliation before start, or by opting CTs into the timer-driven
`nix-auto` reconciler.

### 3. Reconciliation syncs the stage into the guest

The Rust host reconciler owns all payload materialization. It does not install
`/etc/nixos/configuration.nix` and does not bind a managed Nix tree into the
guest. Instead it mirrors the rendered host build input into
`/var/lib/proxnix/build-input/` as a non-authoritative debug snapshot, writes
runtime markers under `/var/lib/proxnix/runtime/`, and copies secret files into
the guest as root-owned regular files.

It syncs staged assets into places such as:

| Stage source | Guest destination |
|-------------|-------------------|
| `bind/config/` | `/var/lib/proxnix/build-input/` |
| `bind/runtime/current-config-hash` | `/var/lib/proxnix/runtime/current-config-hash` |
| `bind/runtime/vmid` | `/var/lib/proxnix/runtime/vmid` |
| `copy/runtime/bin/*` | `/var/lib/proxnix/runtime/bin/` |
| `bind/secrets/*` | `/var/lib/proxnix/secrets/` |

### 4. Reconciler phases activate the desired system

`proxnix-host reconcile` runs on the Proxmox node. It renders the authority wrapper, evaluates
cluster-level `proxnix.containers` through the node view at
`proxnix.nodes.<node>`, skips CTs that are not local according to Proxmox
cluster placement, observes the live `/run/current-system`, and exits early
with `noop-current` when the CT is already on the desired system path. Stale
local CTs are built on the Proxmox node, protected with a host GC root, imported
into the CT, activated by exact system path, verified through
`/run/current-system`, and summarized under `/var/lib/proxnix/status/`.

The phases are split into commands:

- `proxnix-host reconcile build-golden` builds a host-local baseline NixOS closure
  and protects it with a GC root so later CT builds reuse common store paths.
- `proxnix-host reconcile build` evaluates and builds the desired closure.
- `proxnix-host reconcile seed` imports the closure into a running CT for
  explicit online reconciliation.
- `proxnix-host reconcile seed-offline` copies the closure into a mounted stopped CT rootfs, advances `/nix/var/nix/profiles/system`, and writes `/var/lib/proxnix/runtime/next-system` as a compatibility marker.
- `proxnix-host reconcile activate` switches a running CT to the recorded desired system.

For a stopped CT, explicit reconcile builds, syncs config/secrets/runtime files,
and seeds the mounted rootfs from host context without starting it.
`proxnix-host start --vmid <id>` runs that same reconcile path and then starts
the CT. The rootfs system profile is already pointed at the desired closure
before LXC starts, so the CT boots the desired system directly. `next-system`
remains as a compatibility marker for older boot activation flows.
For a running CT, the default explicit reconcile restarts the CT after offline
seed. Use `--online` when uptime is more important than the simpler offline
path; that mode seeds through a
short-lived host-side Unix socket bridge to the container's Nix daemon, then
activates the exact system path from the host. There is no guest activation
timer.

Rollback is intentionally outside reconciliation. `proxnix-host ct rollback`
activates the previous recorded system path as an operational recovery action.
The PVE start/stop path remains independent. If an operator needs Proxnix to
leave the CT alone while the desired state is fixed, set the Proxmox `nix-hold`
tag; normal reconcile refuses held CTs unless `--force` is passed.

Build reuse is optimized per host. Each host should keep a golden-template
build warm so container-specific builds mostly reuse already-realized store
paths. The host uses a durable `flake.lock`. When the workstation publishes
one, the host carries it into `/var/lib/proxnix/authority/flake.lock`; when the
workstation does not publish one, the existing host-managed lock is preserved
and can be advanced by `proxnix-flake-update.timer`. Golden and CT builds
therefore resolve the same pinned nixpkgs revision until that lock changes.
Build failures before seeding leave the CT's current generation untouched.
The authority side repo under `/var/lib/proxnix/authority` is different: the
workstation publishes it already partially rendered, and the host completes the
PVE-derived modules, common modules, flake wrapper, and node manifest before
evaluation. The authoritative operator inputs are `site.nix`,
`containers/<vmid>/`, and the encrypted stores under `/var/lib/proxnix/private`.

## Reconciler State Decision

proxnix keeps its operational state intentionally simple:

- `/var/lib/proxnix/status/<vmid>.json` is the stable operator-facing status
  and compatibility surface. Commands such as `proxnix-host reconcile --status` and
  workstation status views should keep reading it.
- Proxmox tags carry operator intent such as `nix-hold`, `nix-stage`, and
  `nix-auto`.
- Host gcroots carry Nix closure retention state.

Do not move operator-facing status into a hidden local database. The JSON files
are easy to inspect, copy, and recover from. Do not use JSON as the only memory for
multi-step orchestration either; it is not the right place for history, locking,
or retry coordination.

Full host reconciliation is opt-in. Operators and workstation deploys can
trigger explicit reconciliation with `proxnix-host api site-updated`,
`proxnix-host reconcile --vmid <id>`, or
`systemctl start proxnix-reconcile@<id>.service`. The installed
`proxnix-reconcile.timer`
runs `proxnix-host reconcile --auto-tag` daily as a low-frequency safety net. It
builds every local managed CT, then applies Proxmox tag policy. CTs without a
runtime tag are only built. `nix-stage` offline-seeds stopped CTs and leaves
running CTs untouched. `nix-auto` offline-seeds stopped CTs and reconciles
running CTs online. `nix-hold` wins over both runtime tags and blocks runtime
changes after the build.

`proxnix-host api` is the stable host-side API intended for the workstation. It
hides the host layout and delegates to the reconcile engine, so the workstation
only needs to publish files, notify the host that the site changed, and query
status or plans. It does not choose online, offline, build-only, or activation
policy; those decisions stay on the host side.
`proxnix-gc.timer` takes the global reconcile lock, removes stale stage
directories from `/run/proxnix`, keeps the `golden-template` root and one
`<vmid>-desired` root for every CT that is still present on this host, and
prunes desired roots for CTs that moved away or were deleted.
`proxnix-flake-update.timer` advances the host flake lock on the configured
daily, weekly, or monthly cadence, but it does not itself reconcile running CTs.

`current-config-hash` may still appear as diagnostic metadata, but it is not the
activation source of truth.

## Persistent state and experimentation

While `/var/lib/proxnix/build-input/` is refreshed on restart as a debug
snapshot, proxnix **does not use guest Nix config for normal convergence** and
does not touch unrelated parts of the guest rootfs.

- **`/var/lib/`**: Databases and application data stay persistent.
- **`/etc/nixos/local.nix`**: This is an unmanaged sandbox for debugging. A manual `nixos-rebuild test -I nixos-config=/var/lib/proxnix/build-input/configuration.nix` can use it, but normal proxnix convergence does not evaluate config inside the guest.
- **Experimental changes**: Commit final configuration to the workstation-owned site repo, publish it, then run `proxnix deploy` or `proxnix-host reconcile` on the host.

## What the build-input snapshot imports

The debug snapshot entrypoint is intentionally small. It imports:

- `base.nix` — install-layer guest baseline: LXC adjustments, age setup, login summary
- `common.nix` — proxnix option module for the shared operator baseline
- `security-policy.nix` — host-enforced security posture that guest-local overrides should not relax
- optional `site.nix` — site-wide overrides, typically managed from a separate repo
- `proxmox.nix` — generated from PVE conf (hostname, DNS, SSH keys)
- every top-level managed drop-in `*.nix`
- optional `/etc/nixos/local.nix`

That last file is the escape hatch for guest-only experimentation. proxnix does not manage it and does not use it for host-side production builds.

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
