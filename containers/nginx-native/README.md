# nginx native NixOS template

This template runs nginx as a native NixOS service and serves a small static
site from a proxnix-managed path.

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
systemctl status nginx.service
curl http://127.0.0.1:8080/
cat /var/lib/nginx-demo/www/index.html
```

Notes:

- nginx listens on `0.0.0.0:8080`
- the rendered `index.html` is written by `proxnix.secrets.templates`
- this example uses only exercised proxnix features: guest Nix modules, secret
  template rendering, and service restarts on activation
