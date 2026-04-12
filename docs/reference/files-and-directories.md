# Files and Directories

This page maps every important proxnix path by role.

## Repository

| File | Purpose |
|------|---------|
| `install.sh` | Installs local hooks, helpers, and shared cluster files |
| `uninstall.sh` | Removes the local installation from a node |
| `bootstrap.sh` | Records a guest age recipient on the host |
| `yaml-to-nix.py` | Renders managed Nix files from Proxmox and YAML inputs |
| `base.nix` | Shared guest baseline: LXC tweaks, age setup, Podman, login summary |
| `common.nix` | Shared operator baseline module: admin user, SSH, journald, packages |
| `configuration.nix` | Managed NixOS entrypoint imported inside the guest |
| `proxnix-secrets` | Host/workstation secret management tool |
| `proxnix-secrets-guest` | Guest-side secret reader and Podman shell driver |
| `proxnix-doctor` | Host-side health check tool |
| `lxc/hooks/` | Host-side pre-start and mount hooks |
| `containers/` | Workload templates and examples |
| `docs/` | Human-facing documentation site |

## Shared cluster paths (replicated via pmxcfs)

```
/etc/pve/proxnix/
├── base.nix                           shared NixOS baseline
├── common.nix                         shared operator module
├── configuration.nix                  NixOS entrypoint
├── master_age_pubkey                  master recovery key
├── shared_age_pubkey                  shared encryption recipient
└── containers/
    └── <vmid>/
        ├── proxmox.yaml               optional extra PVE fields
        ├── user.yaml                  native service definitions
        ├── age_pubkey                 guest age public key
        ├── dropins/                   extra Nix, services, scripts, Quadlets
        └── quadlets/                  main Podman workload tree

/etc/pve/priv/proxnix/
├── shared_age_identity.txt            shared age private key
├── shared/
│   └── secrets.sops.yaml             shared encrypted secrets
└── containers/
    └── <vmid>/
        └── secrets.sops.yaml         per-container encrypted secrets
```

## Per-node paths (not replicated)

```
/usr/share/lxc/config/
├── nixos.common.conf                  auto-included for ostype=nixos
└── nixos.userns.conf                  auto-included for unprivileged

/usr/share/lxc/hooks/
├── nixos-proxnix-prestart             pre-start render hook
└── nixos-proxnix-mount                mount-time sync hook

/usr/local/lib/proxnix/
├── yaml-to-nix.py                     local runtime helper
├── nixos-proxnix-common.sh            shared hook helper
└── proxnix-secrets-guest              helper injected into guests

/usr/local/sbin/
└── proxnix-doctor                     health check tool
```

## Stage directory on the host (tmpfs)

Created by the pre-start hook, consumed by the mount hook:

```
/run/proxnix/<vmid>/
├── rendered/
│   ├── configuration.nix
│   └── managed/
│       ├── base.nix
│       ├── common.nix
│       ├── proxmox.nix
│       ├── user.nix
│       └── dropins/
├── runtime/
│   ├── systemd/                       *.service files
│   └── bin/                           *.sh, *.py scripts
├── quadlet/                           Quadlet units and app config
├── secrets/
│   ├── shared.sops.yaml
│   └── container.sops.yaml
├── keys/
│   └── shared_identity.txt
└── meta/
    ├── current-config-hash
    ├── vmid
    └── bootstrap_done
```

## Managed paths inside the guest

```
/etc/nixos/
├── configuration.nix                  NixOS entrypoint (read-only)
├── managed/                           host-managed modules (read-only)
│   ├── base.nix
│   ├── common.nix
│   ├── proxmox.nix
│   ├── user.nix
│   └── dropins/
└── local.nix                          guest-only escape hatch (unmanaged)

/etc/proxnix/
├── vmid
├── current-config-hash
├── applied-config-hash
├── proxnix-apply-config-runner
├── secrets/
│   ├── age-keys.txt                   combined age identities
│   ├── shared.sops.yaml
│   └── container.sops.yaml
└── quadlets/                          jj-tracked app config mirror

/etc/age/
├── identity.txt                       container age private key
└── shared_identity.txt                shared age private key

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
└── proxnix-bootstrap.sh              one-time channel bootstrap
```

## Workstation paths

```
~/.config/proxnix/
└── config                             PROXNIX_HOST, PROXNIX_IDENTITY, etc.
```
