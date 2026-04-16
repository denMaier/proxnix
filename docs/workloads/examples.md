# Workload Examples

The repository contains a few useful patterns under `containers/`.

## AdGuard Home: native service with seeded mutable config

`containers/adguard/` shows how to:

- enable a native NixOS service
- keep mutable runtime config under `/opt/adguard`
- seed that config from a templated file and a proxnix secret
- use a dedicated oneshot service to initialize the first real config file

This pattern is useful when the application wants to modify its own YAML at runtime, but you still want proxnix to provide the initial configuration and credentials.

**Setup summary:**

1. Copy `containers/adguard/dropins/` into your container directory
2. Create the required shared secret: `proxnix-secrets set-shared common_adguard_admin_password_hash`
3. Restart the container

## Remote-Connector: single Quadlet container

`containers/twingate/quadlets/twingate.container` is a minimal Quadlet example.

It demonstrates:

- host networking
- secrets injected as environment variables
- always-pull behavior
- a simple long-running service model

**Setup summary:**

1. Copy `containers/twingate/quadlets/` into your container's `quadlets/` directory
2. Create secrets: `proxnix-secrets set <vmid> twingate_access_token` and `proxnix-secrets set <vmid> twingate_refresh_token`
3. Ensure `features: nesting=1` on the CT
4. Restart the container

## Ente: multi-unit Quadlet stack

`containers/ente/` shows a more complete container workload.

It includes:

- a pod and multiple containers
- supporting volumes and network definitions
- extra config files kept next to the units
- a companion README describing required secrets and startup order

**Setup summary:**

1. Copy the whole `containers/ente/` tree into your container directory
2. Create all required secrets (see the Ente README for the list):
   ```bash
   VMID=<vmid>
   proxnix-secrets set "$VMID" ente-pg-pass
   proxnix-secrets set "$VMID" ente-s3-user
   proxnix-secrets set "$VMID" ente-s3-pass
   proxnix-secrets set "$VMID" ente-museum-key
   proxnix-secrets set "$VMID" ente-museum-hash
   proxnix-secrets set "$VMID" ente-museum-jwt-secret
   ```
3. Restart the container
4. Start the services inside the guest (see Ente README for the correct order)

Use it as a reference when you need a non-trivial Podman stack rather than a single container unit.

## Ente native: NixOS module plus native `versitygw`

`containers/ente-native/` shows the native-service version.

It demonstrates:

- using a `dropins/*.nix` module for a nested NixOS service
- running Ente through `services.ente.api` and `services.ente.web`
- running `versitygw` as a native systemd service instead of a container
- materializing proxnix secrets into `/run/ente-secrets/`
- bootstrapping the expected S3 buckets before Museum starts

**Setup summary:**

1. Copy `containers/ente-native/dropins/` into your container directory
2. Edit the placeholder domains in `dropins/ente.nix`
3. Create the required secrets:
   ```bash
   VMID=<vmid>
   proxnix-secrets set "$VMID" ente-s3-user
   proxnix-secrets set "$VMID" ente-s3-pass
   proxnix-secrets set "$VMID" ente-museum-key
   proxnix-secrets set "$VMID" ente-museum-hash
   proxnix-secrets set "$VMID" ente-museum-jwt-secret
   ```
4. Restart the container

Use it when you want the Ente app itself to stay native to NixOS and only need
an S3-compatible gateway beside it.
