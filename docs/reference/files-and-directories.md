# Files and Directories

This page maps every important proxnix path by role.

## Repository

| File | Purpose |
|------|---------|
| `install.sh` | Installs local hooks, helpers, and node-local proxnix files |
| `ansible/install.yml` | Idempotent Ansible playbook that mirrors `install.sh` on one or more Proxmox nodes |
| `uninstall.sh` | Removes the local installation from a node |
| `pve-conf-to-nix.py` | Renders `proxmox.nix` from Proxmox LXC config |
| `proxnix-create-lxc` | Host-side helper to create a proxnix-ready NixOS CT |
| `proxnix-doctor` | Host-side health check tool |
| `proxnix-secrets` | Workstation-side secret and identity management tool |
| `proxnix-publish` | Workstation-side publisher for relay caches |
| `proxnix-workstation-common.sh` | Shared workstation helper library |
| `proxnix-secrets-guest` | Guest-side secret reader and Podman shell driver |
| `remote/codeberg-install.sh` | Curl-friendly wrapper that downloads the repo archive and runs `install.sh` |
| `base.nix` | Shared guest baseline: LXC tweaks, age setup, Podman, login summary |
| `common.nix` | Shared operator baseline module: proxnix options, admin defaults, secrets helpers |
| `security-policy.nix` | Shared host-enforced security policy that is not meant to be relaxed from the guest |
| `configuration.nix` | Managed NixOS entrypoint imported inside the guest |
| `docs/` | Human-facing documentation site |

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
    └── <vmid>/
        ├── dropins/                   extra Nix, services, scripts, Quadlets
        └── quadlets/                  main Podman workload tree

/var/lib/proxnix/private/
├── shared_age_identity.sops.yaml      host-relay-encrypted shared guest identity
├── shared/
│   └── secrets.sops.yaml             shared encrypted secrets
└── containers/
    └── <vmid>/
        ├── age_identity.sops.yaml    host-relay-encrypted container guest identity
        └── secrets.sops.yaml         per-container encrypted secrets

/etc/proxnix/
└── host_relay_identity                shared host relay private key
```

## Per-node runtime paths

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
└── proxnix-secrets-guest              helper injected into guests

/usr/local/sbin/
├── proxnix-create-lxc                 CT creation helper
└── proxnix-doctor                     health check tool
```

## Stage directory on the host (tmpfs)

Created by the pre-start hook, consumed by the mount hook:

```text
/run/proxnix/<vmid>/
├── rendered/
│   ├── configuration.nix
│   └── managed/
│       ├── base.nix
│       ├── common.nix
│       ├── security-policy.nix
│       ├── site.nix
│       ├── proxmox.nix
│       └── dropins/
├── runtime/
│   ├── systemd/                       *.service files
│   └── bin/                           *.sh, *.py scripts
├── quadlet/                           Quadlet units and app config
├── secrets/
│   ├── shared.sops.yaml
│   └── container.sops.yaml
├── keys/
│   ├── identity
│   └── shared_identity.txt
└── meta/
    ├── current-config-hash
    └── vmid
```

## Managed paths inside the guest

```text
/etc/nixos/
├── configuration.nix                  NixOS entrypoint (read-only)
├── managed/                           host-managed modules (read-only)
│   ├── base.nix
│   ├── common.nix
│   ├── security-policy.nix
│   ├── site.nix
│   ├── proxmox.nix
│   └── dropins/
└── local.nix                          guest-only escape hatch (unmanaged)

/etc/proxnix/
├── vmid
├── current-config-hash
├── applied-config-hash
├── proxnix-apply-config-runner
├── secrets/
│   ├── shared.sops.yaml
│   └── container.sops.yaml
└── quadlets/                          jj-tracked app config mirror

/etc/proxnix/secrets/
├── identity                           host-staged container SSH private key used as an age identity
└── shared_identity                    shared SSH private key used as an age identity

/etc/systemd/system.attached/
├── proxnix-apply-config.service
└── <user-defined>.service

/usr/local/bin/
├── proxnix-secrets                    guest secret reader
└── <user-defined scripts>

/etc/containers/systemd/               Quadlet unit files
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
└── private/
    ├── host_relay_identity.sops.yaml
    ├── shared_age_identity.sops.yaml
    ├── shared/
    └── containers/
```
