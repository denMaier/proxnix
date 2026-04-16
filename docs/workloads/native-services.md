# Native Services

Use native services when the application maps well to an existing NixOS module.

## Quick example

```nix
{ lib, ... }: {
  services.jellyfin.enable = true;
  users.users.jellyfin.extraGroups = [ "render" "video" ];
  systemd.services.jellyfin.serviceConfig.PrivateDevices = lib.mkForce false;
}
```

Put that in `containers/<vmid>/dropins/jellyfin.nix`, restart the container, and Jellyfin is running.

## Main input file

The main declaration format is `dropins/*.nix`.

Minimal shape:

```nix
{ ... }: {
  services.jellyfin.enable = true;
}
```

## Typical patterns

### Basic service options

```nix
{ ... }: {
  services.immich = {
    enable = true;
    openFirewall = true;
    port = 2283;
  };
}
```

### Hardware acceleration

```nix
{ lib, ... }: {
  services.jellyfin.enable = true;
  users.users.jellyfin.extraGroups = [ "render" "video" ];
  systemd.services.jellyfin.serviceConfig.PrivateDevices = lib.mkForce false;
}
```

Use that pattern for media services or anything that needs GPU or render node access.

### Unstable package selection

```nix
{ pkgs, ... }: {
  services.immich = {
    enable = true;
    package = pkgs.unstable.immich;
  };
}
```

### Secrets

Declare proxnix secrets that should be extracted into `/run/...` before the service starts.

Example:

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

**Important:** You must also create the secret on the host:

```bash
proxnix-secrets set <vmid> db_password
```

## When to use `dropins/*.nix`

Use a Nix drop-in when:

- a service needs configuration outside `services.<name>.*`
- you need firewall rules, users, tmpfiles, or custom units
- you want to seed configuration files or wire secret paths into unrelated options

## Example pattern

The repository's AdGuard Home example shows a good native-service pattern:

- enable the NixOS module
- keep mutable runtime config outside the immutable managed tree
- seed that runtime config from a proxnix secret on first start

See `containers/adguard/` for a concrete example.

## What not to do

- do not define container workloads in native-service drop-ins
- do not hardcode secrets into Nix
- do not put durable host-managed config into `/etc/nixos/local.nix`
