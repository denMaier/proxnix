# nginx container workload template

This template runs nginx as a Podman container through guest-side Nix config.

Copy this folder's `dropins/` directory into your workstation-owned site repo
at `containers/<vmid>/`.

The real config lives in `dropins/nginx.nix`.

Create the required secret before publishing the container:

```bash
VMID=<vmid>
proxnix-secrets set "$VMID" nginx_index_message
```

Publish the updated site config and secrets, then restart the container:

```bash
proxnix-publish --vmid <vmid>
pct restart <vmid>
```

Useful checks inside the guest:

```bash
systemctl status podman-nginx.service
podman ps -a
curl http://127.0.0.1:8080/
```

Notes:

- this example enables Podman explicitly inside the guest
- the container publishes `127.0.0.1:8080` to guest port `80`
- the served `index.html` is rendered by `proxnix.secrets.templates` and mounted read-only into the container
- this example keeps the guest firewall on and binds only to loopback
