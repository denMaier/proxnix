# Native Services

Use native services when the application maps well to an existing NixOS module.

## Quick example

```yaml
# user.yaml
runtime: native
services:
  jellyfin:
    enable: true
    hardware_acceleration: true
```

That's it. Restart the container and Jellyfin is running.

## Main input file

The main declaration format is `user.yaml`.

Minimal shape:

```yaml
runtime: native
services:
  jellyfin:
    enable: true
```

## Supported keys per service

### `enable`

Required for proxnix to emit the service configuration.

### `options`

Passes through to `services.<name>.<option>`.

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

When `true`, proxnix adds:

- `render` and `video` groups to `users.users.<service>.extraGroups`
- `systemd.services.<service>.serviceConfig.PrivateDevices = lib.mkForce false`

Use it for media services or anything that needs GPU or render node access.

### `unstable_package`

When `true`, proxnix fetches `nixos-unstable` and sets:

```nix
services.<name>.package = pkgs.unstable.<name>
```

Use it only when the stable package is missing a required fix or version.

### `secrets`

Declares proxnix secrets that should be extracted into `/run/...` before the service starts.

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

**Important:** You must also create the secret on the host:

```bash
proxnix-secrets set <vmid> db_password
```

And point the service at the file path, either via `options` or a `dropins/*.nix` file:

```nix
{ ... }: {
  services.immich.database.passwordFile = "/run/immich-secrets/db_password";
}
```

## When to use `dropins/*.nix`

Use a Nix drop-in when:

- a service needs configuration outside `services.<name>.*`
- the YAML representation becomes awkward
- you need firewall rules, users, tmpfiles, or custom units
- you want to seed configuration files or wire secret paths into unrelated options

## Example pattern

The repository's AdGuard Home example shows a good native-service pattern:

- enable the NixOS module
- keep mutable runtime config outside the immutable managed tree
- seed that runtime config from a proxnix secret on first start

See `containers/adguard/` for a concrete example.

## What not to do

- do not define container workloads in `user.yaml`
- do not hardcode secrets into YAML or Nix
- do not put durable host-managed config into `/etc/nixos/local.nix`
