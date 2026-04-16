# Native NixOS deployments for AI agents

This file is the quick reference for generating native NixOS service configs in proxnix.
Use it instead of scanning the repo.

## Decision rule

Choose a native NixOS deployment when the app should run as a NixOS service from nixpkgs rather than as a Podman container.

In this mode, the primary input is one or more `dropins/*.nix` modules.

## Core rule

Native-service config is pure Nix. If the app is containerized, use Quadlets, preferably under `quadlets/`.

## Where files go

For a target container VMID, host-side files live at:

- `/var/lib/proxnix/containers/<vmid>/dropins/*.nix`
- `/var/lib/proxnix/containers/<vmid>/dropins/*.service`
- `/var/lib/proxnix/containers/<vmid>/dropins/*.{sh,py}`

Inside the guest, proxnix generates and imports:

- `/etc/nixos/managed/proxmox.nix` from the Proxmox CT config
- `/etc/nixos/managed/dropins/*.nix` from host `dropins/*.nix`
- `/etc/systemd/system.attached/*.service` from host `dropins/*.service`
- `/usr/local/bin/*.{sh,py}` from host `dropins/*.{sh,py}`
- `/etc/nixos/local.nix` as an optional guest-only escape hatch

## Minimal shape

```nix
{ ... }: {
  services.immich.enable = true;
}
```

## How secrets work for native services

Native service secrets are stored on the host with `proxnix-secrets`.
At runtime, proxnix extracts them through declarative helpers such as `proxnix.secrets.files` into a tmpfs-backed `/run/...` path.

Your generated config must then point the service at that file path in a `dropins/*.nix` module.

Example pattern:

```nix
{ ... }: {
  proxnix.secrets.files.db-password = {
    unit = "proxnix-immich-secrets";
    path = "/run/immich-secrets/db_password";
    owner = "root";
    group = "immich";
    mode = "0640";
    before = [ "immich.service" ];
    wantedBy = [ "immich.service" ];
  };

  services.immich.database.passwordFile = "/run/immich-secrets/db_password";
}
```

## When to use `dropins/*.nix`

Use a Nix drop-in when:

- the service needs attributes outside `services.<name>.*`
- you need custom systemd settings
- you need `environment.etc`, tmpfiles, users, firewall, or package overrides
- you want host-managed config to stay pure Nix

`dropins/*.nix` files are imported automatically by `configuration.nix`.

## What to generate for a new native app

When asked to generate a native deployment, produce:

- one or more `dropins/<app>.nix` modules
- documented secret names if the service needs credentials
- service-specific file references using `/run/...` secret paths or `/etc/...` files created by Nix

## What not to do

- Do not define containers in native-service drop-ins
- Do not hardcode secrets in Nix
- Do not reach for Quadlets if a clean NixOS module already exists and the user asked for a native deployment
- Do not treat `/etc/nixos/local.nix` as the place for host-managed generated config

## Minimal checklist

Before finishing a native app config, verify:

- every managed service has `enable = true;`
- service-specific settings live in `dropins/*.nix`
- secrets are declared and consumed through `/run/...` paths
- no container definitions are mixed into the native-service config
