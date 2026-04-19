# Configuration Model

proxnix deliberately splits configuration between Proxmox-owned data, host-managed proxnix files, generated Nix, and guest-local overrides.

## Sources of truth

### Proxmox CT config

The Proxmox container config remains authoritative for:

- VMID
- hostname
- IP addressing and gateway
- nameservers and search domain from Proxmox
- CT features such as nesting or keyctl when your chosen guest workload needs them
- SSH public keys defined in the WebUI

proxnix reads those values from `/etc/pve/lxc/<vmid>.conf` and turns the relevant subset into `proxmox.nix`.

### `site.nix`

`site.nix` is the host-side site override layer.

Use it for settings that should apply broadly across containers but still belong
to your environment rather than the install repo.

Typical uses:

- extending `proxnix.common.extraPackages`
- changing the shared admin user defaults
- setting a cluster-wide firewall policy, for example `networking.firewall.enable = false;`
- setting node-wide policy overrides without editing the install repo

### `dropins/`

`dropins/` is the primary host-side extension point for per-container Nix configuration.

This is also the default place for Nix-authored Quadlet workloads when you use
a Nix module layer such as `quadlet-nix`.

Supported file types:

| Extension | What happens |
|-----------|-------------|
| `*.nix` | Imported from `configuration.nix` as extra NixOS modules |
| `*.service` | Rejected; move the unit into `dropins/*.nix` so it stays guest Nix-managed |
| `*.sh`, `*.py` | Copied to `/var/lib/proxnix/runtime/bin/` and exposed on `PATH` |
| `*.container`, `*.volume`, `*.network`, `*.pod`, `*.image`, `*.build` | Rejected; raw host-side Quadlet staging is no longer supported |
| Subdirectories | Copied into the managed drop-ins tree (for Nix files or assets they reference) |

Top-level `dropins/*.nix` files are still the auto-imported entrypoints. proxnix
does not auto-import `dropins/<dir>/default.nix`; use subdirectories for assets
or import them explicitly from a top-level drop-in.

### `containers/_template/`

`containers/_template/` is an optional shared host-side template tree for
managed Nix snippets that should be visible to more than one container.

Templates are selected per-container by marker files under
`containers/<vmid>/templates/*.template`.

For example:

```text
containers/
  _template/
    postgres-common/
      default.nix
  123/
    templates/
      postgres-common.template
    dropins/
      app.nix
```

The marker file just selects which shared template tree proxnix stages into
`/var/lib/proxnix/config/managed/_template/` for that guest. Your container drop-in still
imports it explicitly, for example:

```nix
imports = [ ../_template/postgres-common ];
```

Using `default.nix` inside a template directory keeps the import short while
still allowing each template to carry helper files beside it.

### `quadlets/`

Raw host-side `quadlets/` trees are no longer supported.

If you previously used them, migrate the workload into `dropins/*.nix` and
import any shared container module layer, such as `quadlet-nix`, from
`site.nix`.

## Generated files

`pve-conf-to-nix.py` generates:

- `proxmox.nix`

The shared entrypoint imports them from `/var/lib/proxnix/config/managed/`, after the install
layer (`base.nix`, `common.nix`) and optional `site.nix`.

## When to use which mechanism

### Use Proxmox WebUI when

- you are changing IP, gateway, nameservers, hostname, cores, RAM, or CT features
- you are adding SSH keys that naturally belong to the container definition

### Use `dropins/*.nix` when

- a native NixOS module already covers the application
- you need options outside a single `services.<name>.*` subtree
- you need custom systemd, firewall, tmpfiles, users, or `environment.etc`
- you want host-managed config to stay pure Nix

## Firewall default

NixOS enables the firewall by default, so proxnix-managed guests start with it
on unless you override it. The usual pattern is to keep it on and open only
the ports a workload needs, for example:

```nix
networking.firewall.allowedTCPPorts = [ 8080 ];
```

If you really want to disable it across the whole published site or cluster,
set this in `site.nix`:

```nix
{ ... }: {
  networking.firewall.enable = false;
}
```

If you want to disable it only for one container, set the same option in one
of that container's `dropins/*.nix` files.

### Use container modules when

- the application is container-first
- a clean native NixOS module does not exist or is not what you want

Express those workloads in `dropins/*.nix` through a dedicated Nix container
module layer such as `quadlet-nix`.

### Use `/etc/nixos/local.nix` when

- you are doing guest-local experiments before committing them to the host
- you need a temporary override for debugging
- you have guest-specific state you don't want to manage from the host

`local.nix` is the only file inside `/etc/nixos/` that is **never overwritten by proxnix**. You can use it to test configuration changes without needing to restart the container for every iteration. Once your config works, move it into host-side `dropins/*.nix` to make it permanent.

Do not treat `local.nix` as a trusted security-policy layer. Host-managed
security settings should live in `site.nix`, generated `proxmox.nix`, or
host-side `dropins/*.nix`.
