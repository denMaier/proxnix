# Configuration Model

proxnix deliberately splits configuration between Proxmox-owned data, host-managed proxnix files, generated Nix, and guest-local overrides.

## Sources of truth

### Proxmox CT config

The Proxmox container config remains authoritative for:

- VMID
- hostname
- IP addressing and gateway
- nameservers injected by Proxmox
- CT features such as `nesting=1`
- SSH public keys defined in the WebUI

proxnix reads those values from `/etc/pve/lxc/<vmid>.conf` and turns the relevant subset into `proxmox.nix`.

### `proxmox.yaml`

Use `proxmox.yaml` only for fields that the WebUI cannot express.

Typical uses:

- `search_domain`
- extra `ssh_keys`

Do not try to redefine the CT's main IP or gateway here when Proxmox already owns those values.

### `site.nix`

`site.nix` is the host-side site override layer.

Use it for settings that should apply broadly across containers but still belong
to your environment rather than the install repo.

Typical uses:

- extending `proxnix.common.extraPackages`
- changing the shared admin user defaults
- setting node-wide policy overrides without editing the install repo

### `user.yaml`

`user.yaml` is for native NixOS services only.

It supports:

- `runtime: native`
- `services:` entries

It does **not** support container definitions. Use Quadlets for that.

### `dropins/`

`dropins/` is the extension point for cases that do not fit neatly into `user.yaml`.

Supported file types:

| Extension | What happens |
|-----------|-------------|
| `*.nix` | Imported from `configuration.nix` as extra NixOS modules |
| `*.service` | Attached under `/etc/systemd/system.attached/` |
| `*.sh`, `*.py` | Copied to `/usr/local/bin/` |
| `*.container`, `*.volume`, `*.network`, `*.pod`, `*.image`, `*.build` | Treated as Quadlet units |
| Subdirectories | Copied into the managed drop-ins tree (for Nix files or assets they reference) |

### `quadlets/`

Use `quadlets/` for the main Podman workload tree.

proxnix copies:

- top-level Quadlet unit files into `/etc/containers/systemd/`
- the whole tree into `/etc/proxnix/quadlets/` for app config and nearby assets

This is the preferred location for container-first workloads. Keep writable state out of this tree; put runtime data under `/var/lib/<app>/...` instead.

### `dropins/` vs `quadlets/` — when to use which

Both can hold Quadlet unit files, but they serve different roles:

| | `dropins/` | `quadlets/` |
|-|-----------|-------------|
| **Primary use** | Nix modules, attached systemd units, scripts, and occasional supporting Quadlet units | Main Podman workload tree |
| **Mirroring** | Quadlet unit files are copied into `/etc/containers/systemd/`; non-unit files stay in the managed drop-ins tree | Full tree mirrored to `/etc/proxnix/quadlets/`, with top-level unit files also copied into `/etc/containers/systemd/` |
| **Best for** | A small supporting container alongside native services | A workload that is primarily container-based |

**Rule of thumb:** If the app is container-first, use `quadlets/`. Use `dropins/` for Nix integration and small supporting extras.

## Generated files

`yaml-to-nix.py` generates:

- `proxmox.nix`
- `user.nix`

The shared entrypoint imports them from `/etc/nixos/managed/`, after the install
layer (`base.nix`, `common.nix`) and optional `site.nix`.

## When to use which mechanism

### Use Proxmox WebUI when

- you are changing IP, gateway, nameservers, hostname, cores, RAM, or CT features
- you are adding SSH keys that naturally belong to the container definition

### Use `proxmox.yaml` when

- Proxmox has no UI field for the setting
- the data still conceptually belongs to base machine identity

### Use `user.yaml` when

- a native NixOS module already covers the application
- the configuration maps cleanly to `services.<name>.*`

### Use `dropins/*.nix` when

- you need options outside `services.<name>.*`
- the Nix shape is awkward in YAML
- you need custom systemd, firewall, tmpfiles, users, or `environment.etc`

### Use Quadlets when

- the application is container-first
- a clean native NixOS module does not exist or is not what you want

### Use `/etc/nixos/local.nix` when

- you are doing guest-local experiments before committing them to the host
- you need a temporary override for debugging
- you have guest-specific state you don't want to manage from the host

`local.nix` is the only file inside `/etc/nixos/` that is **never overwritten by proxnix**. You can use it to test configuration changes without needing to restart the container for every iteration. Once your config works, move it into `user.yaml` or `dropins/` on the host to make it permanent.
