# Ente container template

Copy this folder into `/var/lib/proxnix/containers/<vmid>/` for the target
container.

- `quadlets/*.container`, `*.network`, `*.pod`, and `*.volume` are the raw
  Quadlets proxnix copies into `/etc/containers/systemd/` inside the guest.
- `quadlets/museum.yaml` and `quadlets/s3-init.sh` live beside the unit files
  on the host and are mirrored into `/etc/proxnix/quadlets/` inside the guest.

Create these per-container secrets before starting the stack:

```bash
VMID=<vmid>

proxnix-secrets set "$VMID" ente-pg-pass
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

Then start the units inside the guest:

```bash
systemctl start \
  ente-postgres.service \
  ente-versitygw.service \
  ente-web.service \
  ente-museum.service \
  ente-s3-init.service
```
