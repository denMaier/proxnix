# Native NixOS deployments for AI agents

This file is the quick reference for generating **native NixOS service configs** in proxnix.
Use it instead of scanning the repo.

## Decision rule

Choose a **native NixOS deployment** when the app should run as a NixOS service from nixpkgs rather than as a Podman container.

In this mode, the primary input is `user.yaml`.

## Core rule

`user.yaml` supports only:

- `runtime: native`
- `services:` entries for native NixOS services

It does **not** support container definitions. If the app is containerized, use Quadlets, preferably under `quadlets/`.

## Where files go

For a target container VMID, host-side files live at:

- `/etc/pve/proxnix/containers/<vmid>/user.yaml`
- `/etc/pve/proxnix/containers/<vmid>/dropins/*.nix`
- `/etc/pve/proxnix/containers/<vmid>/dropins/*.service`
- `/etc/pve/proxnix/containers/<vmid>/dropins/*.{sh,py}`
- optional `/etc/pve/proxnix/containers/<vmid>/proxmox.yaml`

Inside the guest, proxnix generates and imports:

- `/etc/nixos/managed/user.nix` from `user.yaml`
- `/etc/nixos/managed/dropins/*.nix` from host `dropins/*.nix`
- `/etc/systemd/system.attached/*.service` from host `dropins/*.service`
- `/usr/local/bin/*.{sh,py}` from host `dropins/*.{sh,py}`
- `/etc/nixos/local.nix` as an optional guest-only escape hatch

## `user.yaml` schema

Minimal shape:

```yaml
runtime: native
services:
  <service-name>:
    enable: true
```

Supported per-service keys:

### `enable`

Required to actually emit config.
Set to `true` for the service to be configured.

### `options`

Passes through to `services.<name>.<option>` in NixOS.
Use this for service-specific module options.

Example:

```yaml
runtime: native
services:
  immich:
    enable: true
    options:
      openFirewall: true
      port: 2283
```

### `hardware_acceleration`

If `true`, proxnix adds:

- `users.users.<name>.extraGroups = [ "render" "video" ]`
- `systemd.services.<name>.serviceConfig.PrivateDevices = lib.mkForce false`

Use this for services like media servers that need GPU access.

### `unstable_package`

If `true`, proxnix pulls that service package from `nixos-unstable` and sets:

- `services.<name>.package = pkgs.unstable.<name>`

Use only when the stable channel is missing a required fix or version.

### `secrets`

Declares proxnix secrets that proxnix extracts from the staged SOPS YAML store in `ExecStartPre`.

Example:

```yaml
runtime: native
services:
  immich:
    enable: true
    secrets:
      - name: db_password
        path: /run/immich-secrets/db_password
```

If `path` is omitted, proxnix defaults to `/run/<service>-secrets/<secret-name>`.

## How secrets work for native services

Native service secrets are stored on the host with `proxnix-secrets`.
At runtime, proxnix extracts them through the guest-side `proxnix-secrets` helper into a tmpfs-backed `/run/...` path.

Your generated config must then point the service at that file path, either:

- directly in `user.yaml` via `options`, if the module supports a `passwordFile`-style option
- or in a `dropins/*.nix` file when more control is needed

Example pattern:

```nix
{ ... }:
{
  services.immich.database.passwordFile = "/run/immich-secrets/db_password";
}
```

## When to use `dropins/*.nix`

Use a Nix drop-in when:

- the service needs an option shape that is awkward in YAML
- you need to set attributes outside `services.<name>.*`
- you need custom systemd settings
- you need `environment.etc`, tmpfiles, users, firewall, or package overrides beyond the generic schema

`dropins/*.nix` files are imported automatically by `configuration.nix`.

## `proxmox.yaml`

Use `proxmox.yaml` only for fields the Proxmox WebUI cannot express, such as:

- `search_domain`
- `ssh_keys`

Do not try to define service configuration there.

## What to generate for a new native app

When asked to generate a native deployment, produce:

- a `user.yaml` entry under `services:`
- optional `dropins/<app>.nix` for advanced settings
- documented secret names if the service needs credentials
- any service-specific file references using `/run/...` secret paths or `/etc/...` files created by Nix

## What not to do

- Do not define containers in `user.yaml`
- Do not hardcode secrets in YAML or Nix
- Do not reach for Quadlets if a clean NixOS module already exists and the user asked for a native deployment
- Do not treat `/etc/nixos/local.nix` as the place for host-managed generated config

## Minimal checklist

Before finishing a native app config, verify:

- `runtime: native` is present
- every service entry has `enable: true`
- service-specific settings are under `options:` or a `dropins/*.nix` file
- secrets are declared and consumed through `/run/...` paths
- no container definitions appear in `user.yaml`
