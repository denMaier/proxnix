# Files and Directories

This page maps every important proxnix path by role.

## Repository

| File | Purpose |
|------|---------|
| `host/install.sh` | Installs local hooks, helpers, and node-local proxnix files |
| `host/uninstall.sh` | Repo-local source for the uninstall logic shipped onto hosts as `proxnix-uninstall` |
| `host/ansible/install.yml` | Idempotent Ansible playbook that mirrors `host/install.sh` on one or more Proxmox nodes |
| `host/inventory.proxmox.ini` | Example Ansible inventory for remote Proxmox installs |
| `host/remote/codeberg-install.sh` | Curl-friendly wrapper that downloads the repo archive and runs `host/install.sh` |
| `host/remote/install-host-package.sh` | Curl-friendly installer for the published `proxnix-host` Debian package |
| `host/packaging/` | Debian packaging scripts and maintainer-script templates for the host runtime |
| `VERSION` | Canonical project release version used for tags and packaging checks |
| `ci/project-version.sh` | Prints the canonical version from `VERSION` |
| `ci/release-lib.sh` | Shared shell helpers for tag validation and release tagging |
| `ci/bump-version.sh` | Bumps `major`, `minor`, or `patch` in `VERSION` and `workstation/pyproject.toml` |
| `ci/set-version.sh` | Updates `VERSION` and `workstation/pyproject.toml` together |
| `ci/release.sh` | One-command version bump, release commit, annotated tag, and push |
| `ci/release-tag.sh` | One-command annotated release tag creator and optional pusher |
| `ci/install-git-hooks.sh` | Installs the repo-managed git hooks via `core.hooksPath` |
| `ci/install-workstation.sh` | Installs or upgrades the workstation Python package with pip |
| `ci/workstation-version.sh` | Prints the workstation package version from `workstation/pyproject.toml` |
| `host/pve-conf-to-nix.py` | Renders `proxmox.nix` from Proxmox LXC config |
| `host/proxnix-create-lxc` | Host-side helper to create a proxnix-ready NixOS CT |
| `host/proxnix-doctor` | Host-side health check tool |
| `host/proxnix-secrets-guest` | Guest-side secret reader and Podman shell driver |
| `host/base.nix` | Shared guest baseline: LXC tweaks, age setup, login summary |
| `host/common.nix` | Shared operator baseline module: proxnix options, admin defaults, and secret lifecycles |
| `host/security-policy.nix` | Shared host-enforced security policy that is not meant to be relaxed from the guest |
| `host/configuration.nix` | Managed NixOS entrypoint imported inside the guest |
| `host/system/` | Extra host-side systemd units, mounts, timers, and udev rules |
| `workstation/bin/proxnix` | Repo-local wrapper for the unified workstation CLI |
| `workstation/bin/proxnix-secrets` | Repo-local wrapper for the workstation secret and identity tool |
| `workstation/bin/proxnix-publish` | Repo-local wrapper for relay-cache publishing |
| `workstation/bin/proxnix-doctor` | Repo-local wrapper for site lint and drift checking |
| `workstation/bin/proxnix-lxc-exercise` | Repo-local wrapper for the automated LXC exercise lab |
| `workstation/bin/proxnix-tui` | Repo-local wrapper for the terminal UI |
| `workstation/legacy/proxnix-workstation-common.sh` | Retained shell-era helper library for compatibility |
| `workstation/flake.nix` | Nix package and module exports for workstation tooling |
| `workstation/nix/` | Workstation package definitions and shared NixOS/nix-darwin module |
| `workstation/packaging/` | Workstation packaging scripts used by CI and release builds |
| `workstation/src/` | Publishable Python package source |
| `workstation/apps/ProxnixManager/` | SwiftUI macOS app |
| `.forgejo/workflows/host-packages.yml` | Self-hosted Forgejo Actions workflow for host Debian package builds |
| `.forgejo/workflows/workstation-packages.yml` | Self-hosted Forgejo Actions workflow for workstation Python package builds and PyPI publishing |
| `.githooks/` | Repo-managed git hooks, currently release-tag validation on push |
| `docs/ai/` | AI-agent-focused reference notes and evaluations |
| `docs/` | Human-facing documentation site |

Current top-level layout:

```text
.
├── host/
│   ├── install.sh
│   ├── uninstall.sh
│   ├── ansible/install.yml
│   ├── inventory.proxmox.ini
│   ├── packaging/
│   ├── remote/codeberg-install.sh
│   ├── lxc/
│   ├── system/
│   ├── systemd/
│   ├── pve-conf-to-nix.py
│   ├── proxnix-create-lxc
│   ├── proxnix-doctor
│   ├── proxnix-secrets-guest
│   ├── base.nix
│   ├── common.nix
│   ├── security-policy.nix
│   └── configuration.nix
├── workstation/
│   ├── flake.nix
│   ├── apps/ProxnixManager/
│   ├── bin/
│   ├── legacy/
│   ├── nix/
│   ├── packaging/
│   └── src/
├── ci/
├── .githooks/
├── docs/ai/
├── containers/
├── docs/
└── mkdocs.yml
```

## Node-local host paths

These paths are the published host-side state on the Proxmox node. The workstation-owned site repo is the source of truth.

```text
/var/lib/proxnix/
├── base.nix                           shared NixOS baseline
├── common.nix                         shared operator module
├── security-policy.nix                host-enforced security policy
├── configuration.nix                  NixOS entrypoint
├── site.nix                           published site override
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

## Per-node runtime paths

Package-installed nodes get the hook and helper paths below. The
`install-manifest.txt`, `install-info.txt`, and `proxnix-uninstall` entries are
specific to the shell-installer path.

```text
/usr/share/lxc/config/
├── nixos.common.conf                  auto-included for ostype=nixos
└── nixos.userns.conf                  auto-included for unprivileged

/usr/share/lxc/hooks/
├── nixos-proxnix-prestart             pre-start render hook
├── nixos-proxnix-mount                mount-time sync hook
└── nixos-proxnix-poststop             post-stop cleanup hook

/usr/local/lib/proxnix/
├── pve-conf-to-nix.py                 local runtime helper
├── nixos-proxnix-common.sh            shared hook helper
├── proxnix-secrets-guest              helper injected into guests
├── install-manifest.txt               installed-file manifest
└── install-info.txt                   local install metadata

/usr/local/sbin/
├── proxnix-create-lxc                 CT creation helper
├── proxnix-doctor                     health check tool
└── proxnix-uninstall                  local uninstall helper
```

## Stage directory on the host (tmpfs)

Created by the pre-start hook. The mount hook binds the managed config/runtime
markers from here, copies guest-visible files into place, copies secret files
into the guest as root-owned regular files, and the post-stop hook removes the
tree after the container stops:

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
    │   ├── proxnix-apply-config-runner
    │   └── bin/
    └── etc/
        ├── nixos/configuration.nix
        └── systemd/system.attached/proxnix-apply-config.service
```

## Managed paths inside the guest

```text
/etc/nixos/
├── configuration.nix                  copied host-managed entrypoint
└── local.nix                          guest-only escape hatch (unmanaged)

/var/lib/proxnix/
├── config/
│   └── managed/                       host-managed modules (read-only bind mount)
│       ├── base.nix
│       ├── common.nix
│       ├── security-policy.nix
│       ├── site.nix
│       ├── proxmox.nix
│       ├── _template/                 selected shared Nix templates (read-only)
│       └── dropins/
├── runtime/
│   ├── vmid
│   ├── current-config-hash
│   ├── applied-config-hash
│   ├── proxnix-apply-config-runner
│   ├── bin/
│   │   ├── proxnix-secrets
│   │   └── <user-defined scripts>
│   └── manifests/
└── secrets/
    ├── effective.sops.yaml            encrypted compiled container secret store
    └── identity                       container SSH private key

/etc/systemd/system.attached/
├── proxnix-apply-config.service
└── <user-defined>.service

/etc/secrets/.ids/                     Podman secret ID→name mappings
/var/lib/containers/storage/secrets/
└── secrets.json                       Podman secret registry

/root/
└── proxnix-bootstrap.sh              manual recovery helper for first rebuild
```

## Workstation paths

```text
~/.config/proxnix/
└── config                             PROXNIX_SITE_DIR, PROXNIX_MASTER_IDENTITY, etc.

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
