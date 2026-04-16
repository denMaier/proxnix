# Ente native NixOS template

This template runs Ente through the NixOS module and keeps `versitygw` as a
native systemd service.

Copy this folder's `dropins/` directory into
`/var/lib/proxnix/containers/<vmid>/` for the target container.

The real config lives in `dropins/ente.nix`.

Before starting the container, adjust the placeholder domains in
`dropins/ente.nix`.

Create these per-container secrets before restarting the container:

```bash
VMID=<vmid>

proxnix-secrets set "$VMID" ente-s3-user
proxnix-secrets set "$VMID" ente-s3-pass
proxnix-secrets set "$VMID" ente-museum-key
proxnix-secrets set "$VMID" ente-museum-hash
proxnix-secrets set "$VMID" ente-museum-jwt-secret
```

Suggested generators:

```bash
openssl rand -base64 21
openssl rand -base64 32
openssl rand -base64 64
openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
```

After copying the folder and creating the secrets, restart the container:

```bash
pct restart <vmid>
```

The native services will be enabled automatically on rebuild. Useful checks
inside the guest:

```bash
systemctl status versitygw.service
systemctl status proxnix-ente-buckets.service
systemctl status ente.service
systemctl status nginx.service
```

Notes:

- This variant uses the NixOS-managed local PostgreSQL database, so there is no
  separate database password secret.
- `versitygw` listens only on `127.0.0.1:3200`; Ente talks to it locally.
- The bucket bootstrap unit creates the three buckets Ente expects on every
  boot and exits successfully if they already exist.
