# Files and Directories

This page maps every important proxnix path by role.

## Repository

| File | Purpose |
|------|---------|
| `flake.nix` | Root host-runtime flake exposing `.#proxnix-host` |
| `host/nix/proxnix-host.nix` | Nix package for the Proxmox host payload |
| `host/install/uninstall.sh` | Repo-local source for the uninstall logic shipped onto hosts as `proxnix-host-uninstall` |
| `host/deploy/ansible/install.yml` | Idempotent Ansible playbook that installs proxnix on one or more Proxmox nodes |
| `host/deploy/inventory.proxmox.ini` | Example Ansible inventory for remote Proxmox installs |
| `VERSION` | Canonical project release version used for tags and packaging checks |
| `ci/project-version.sh` | Prints the canonical version from `VERSION` |
| `ci/release-lib.sh` | Shared shell helpers for tag validation and release tagging |
| `ci/bump-version.sh` | Bumps `major`, `minor`, or `patch` in `VERSION` and `workstation/cli/pyproject.toml` |
| `ci/set-version.sh` | Updates `VERSION` and `workstation/cli/pyproject.toml` together |
| `ci/release.sh` | One-command version bump, release commit, annotated tag, and push |
| `ci/release-tag.sh` | One-command annotated release tag creator and optional pusher |
| `ci/install-git-hooks.sh` | Installs the repo-managed git hooks via `core.hooksPath` |
| `ci/install-workstation.sh` | Installs or upgrades the workstation Python package with pip |
| `ci/render-homebrew-cask.sh` | Renders a concrete Homebrew cask for Proxnix Manager from the template |
| `ci/render-homebrew-formula.sh` | Renders a concrete Homebrew formula for `proxnix-workstation` from the template |
| `ci/workstation-version.sh` | Prints the workstation package version from `workstation/cli/pyproject.toml` |
| `Cargo.toml` | Root Cargo workspace for host-side Rust crates |
| `crates/proxnix-host/` | Rust source for the `proxnix-host` controller binary |
| `host/runtime/bin/proxnix-doctor` | Host-side shell health check tool |
| `host/runtime/bin/proxnix-host-activate` | Host activation helper that links profile payloads into host paths |
| `host/runtime/lib/proxnix-secrets-guest` | Guest-side secret reader and Podman shell driver |
| `host/runtime/systemd/proxnix-flake-update.service` | Host-side flake lock update service |
| `host/runtime/systemd/proxnix-flake-update.timer` | Daily timer that gates daily, weekly, or monthly flake updates |
| `host/runtime/systemd/proxnix-reconcile.service` | Timer target for automatic host event reconciliation |
| `host/runtime/systemd/proxnix-reconcile.timer` | Daily host event sweep: build local managed CTs and apply `nix-stage` / `nix-auto` runtime policy |
| `host/runtime/systemd/proxnix-reconcile@.service` | Explicit per-VMID running-CT reconcile service |
| `host/runtime/lib/proxnix-secrets-guest` | Guest-side secret reader and Podman shell driver |
| `host/runtime/nix/base.nix` | Shared guest baseline: LXC tweaks, age setup, login summary |
| `host/runtime/nix/common.nix` | Shared operator baseline module: proxnix options, admin defaults, and secret lifecycles |
| `host/runtime/nix/security-policy.nix` | Shared host-enforced security policy that is not meant to be relaxed from the guest |
| `host/runtime/nix/configuration.nix` | Managed NixOS entrypoint imported inside the guest |
| `host/extras/system/` | Extra host-side systemd units, mounts, timers, and udev rules |
| `workstation/cli/bin/proxnix` | Repo-local wrapper for the unified workstation CLI |
| `workstation/cli/bin/proxnix-secrets` | Repo-local wrapper for the workstation secret and identity tool |
| `workstation/cli/bin/proxnix-publish` | Repo-local wrapper for relay-cache publishing |
| `workstation/cli/bin/proxnix-doctor` | Repo-local wrapper for site lint and drift checking |
| `workstation/cli/bin/proxnix-lxc-exercise` | Repo-local wrapper for the automated LXC exercise lab |
| `workstation/cli/bin/proxnix-tui` | Repo-local wrapper for the terminal UI |
| `workstation/cli/legacy/proxnix-workstation-common.sh` | Retained shell-era helper library for compatibility |
| `workstation/flake.nix` | Nix package and module exports for workstation tooling |
| `workstation/nix/` | Workstation package definitions and shared NixOS/nix-darwin module |
| `workstation/packaging/` | Workstation packaging scripts used by CI and release builds |
| `workstation/cli/src/` | Publishable Python package source |
| `workstation/manager/` | Proxnix Manager desktop app and hosted web UI |
| `packaging/homebrew/` | Homebrew tap scaffolds for the `proxnix-workstation` formula and Proxnix Manager cask |
| `.github/workflows/pypi-publish.yml` | GitHub Actions workflow for workstation Python package builds and PyPI publishing |
| `.github/workflows/proxnix-manager-dmg.yml` | GitHub Actions workflow for Proxnix Manager DMG builds and release assets |
| `.github/workflows/proxnix-manager-linux.yml` | GitHub Actions workflow for Proxnix Manager Linux archive builds and release assets |
| `.githooks/` | Repo-managed git hooks, currently release-tag validation on push |
| `docs/ai/` | AI-agent-focused reference notes and evaluations |
| `docs/` | Human-facing documentation site |

Current top-level layout:

```text
.
├── host/
│   ├── uninstall.sh
│   ├── install/
│   ├── runtime/
│   │   ├── lxc/config/
│   │   ├── lib/
│   │   ├── bin/
│   │   ├── nix/
│   │   └── systemd/
│   ├── deploy/
│   │   ├── ansible/install.yml
│   │   └── inventory.proxmox.ini
│   └── extras/system/
├── workstation/
│   ├── flake.nix
│   ├── cli/
│   ├── manager/
│   ├── nix/
│   └── packaging/
├── packaging/homebrew/
├── ci/
├── .githooks/
├── docs/ai/
├── containers/
├── docs/
└── mkdocs.yml
```

## Node-local host paths

These paths are the published host-side state on the Proxmox node. The
workstation-owned site repo is the source of truth for configuration; the flake
lock can also be advanced on the host by `proxnix-host flake-update`.

```text
/var/lib/proxnix/
├── base.nix                           shared NixOS baseline
├── common.nix                         shared operator module
├── security-policy.nix                host-enforced security policy
├── configuration.nix                  NixOS entrypoint
├── flake.lock                         host-managed Nix input lock
├── site.nix                           published site override
├── authority/                         generated host authority flake wrapper
├── status/                            reconciler status JSON
├── state/
│   └── flake-update.last-success      last successful flake update timestamp
├── gcroots/
│   └── deploy/
│       └── <vmid>-desired             host GC root for desired closure
└── containers/
    ├── _template/                     shared managed Nix template snippets
    └── <vmid>/
        ├── dropins/                   extra Nix, services, and scripts
        ├── templates/                 `*.template` selectors for shared templates

/var/lib/proxnix/private/
└── containers/
    └── <vmid>/
        ├── age_identity.sops.yaml     host-relay-encrypted container guest identity
        └── effective.sops.yaml        encrypted compiled container secret store

/etc/proxnix/
└── host_relay_identity                shared host relay private key
```

`authority/` is generated state. It is rebuilt from the published host inputs,
container directories, and observed PVE LXC configs, then evaluated by Nix so
the host can expose `nixosConfigurations.ct<vmid>` and
`proxnix.nodes.<node>`. It is not a user-authored database; deleting it should
only force regeneration.

The host relay identity and `shared_age_identity.sops.yaml` remain host-only.
Guests receive their own per-container identity at
`/var/lib/proxnix/secrets/identity` when one is configured, plus the compiled
runtime secret store.

## Per-node runtime paths

Production Ansible installs build the host profile from the configured flake ref
and run `proxnix-host-activate`. Development installs through
`host/deploy/ansible/install-local.yml` first stage the current checkout under
`/var/lib/proxnix/install-source`. The paths below are symlinks into the
`/nix/var/nix/profiles/proxnix-host` profile, except for the local manifest and
install metadata.

```text
/usr/share/lxc/config/
├── nixos.common.conf                  auto-included for ostype=nixos
└── nixos.userns.conf                  auto-included for unprivileged

/usr/share/lxc/hooks/
└── nixos-proxnix-start-host           symlink to `proxnix-host`

/usr/local/lib/proxnix/
├── proxnix-secrets-guest              helper injected into guests
├── install-manifest.txt               installed-file manifest
└── install-info.txt                   local install metadata

/usr/local/sbin/
├── proxnix-host                       Rust host controller
├── proxnix-doctor                     shell health check tool
├── proxnix-host-activate              links the Nix profile payload into host paths
└── proxnix-host-uninstall             local uninstall helper
```

## Stage directory on the host (tmpfs)

Created by the reconciler while rendering a CT payload. The reconciler copies
the rendered build input from here into a guest debug snapshot, writes runtime
markers and guest-visible helper files, copies secret files into the guest as
root-owned regular files, and `proxnix-host gc` removes stale stage trees:

```text
/run/proxnix/<vmid>/
├── bind/
│   ├── config/
│   │   ├── configuration.nix
│   │   └── managed/
│   │       ├── base.nix
│   │       ├── common.nix
│   │       ├── security-policy.nix
│   │       ├── site.nix
│   │       ├── proxmox.nix
│   │       ├── _template/             selected shared templates only
│   │       └── dropins/
│   ├── runtime/
│   │   ├── current-config-hash
│   │   └── vmid
│   └── secrets/
│       ├── effective.sops.yaml
│       └── identity
└── copy/
    ├── runtime/
    │   └── bin/
    └── etc/
```

## Managed paths inside the guest

```text
/etc/nixos/
└── local.nix                          guest-only escape hatch (unmanaged)

/var/lib/proxnix/
├── build-input/                       Rust-mirrored debug snapshot, not activation authority
│   ├── configuration.nix
│   └── managed/
│       ├── base.nix
│       ├── common.nix
│       ├── security-policy.nix
│       ├── site.nix
│       ├── proxmox.nix
│       ├── _template/                 selected shared Nix templates (read-only)
│       └── dropins/
├── runtime/
│   ├── vmid
│   ├── current-config-hash              diagnostic hash, not activation authority
│   ├── bin/
│   │   ├── proxnix-secrets
│   │   └── <user-defined scripts>
│   └── manifests/
└── secrets/
    ├── effective.sops.yaml            encrypted compiled container secret store
    └── identity                       container SSH private key

/etc/secrets/.ids/                     Podman secret ID→name mappings
/var/lib/containers/storage/secrets/
└── secrets.json                       Podman secret registry
```

## Workstation paths

```text
~/.config/proxnix/
└── config                             PROXNIX_SITE_DIR, provider settings, etc.

<proxnix-site>/
├── site.nix
├── containers/
│   └── <vmid>/
│       └── secret-groups.list
└── private/
    ├── host_relay_identity.sops.yaml
    ├── shared/
    ├── groups/
    └── containers/
```
