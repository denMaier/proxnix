# Workload Examples

The repository contains a few useful patterns under `containers/`.

## nginx native: NixOS module plus secret-rendered page

`containers/nginx-native/` shows how to:

- enable a native NixOS service
- serve a small static site from `/var/lib/nginx-demo/www`
- render `index.html` through `proxnix.secrets.templates`
- reload nginx automatically when the activation-time template changes

This pattern is useful when you want the simplest possible native-service demo
for proxnix: one module, one secret, one HTTP endpoint.

**Setup summary:**

1. Copy `containers/nginx-native/dropins/` into your site repo under `containers/<vmid>/`
2. Create the required container secret: `proxnix-secrets set <vmid> nginx_index_message`
3. Publish config and secrets: `proxnix-publish --vmid <vmid>`
4. Restart the container: `pct restart <vmid>`

## nginx container: guest-side Podman workload

`containers/nginx-container/` shows the container-workload version.

It demonstrates:

- enabling Podman explicitly inside the guest
- defining a Quadlet-backed container from `dropins/*.nix`
- rendering a secret-backed `index.html` and mounting it read-only into the container
- publishing guest port `80` on `127.0.0.1:8080`
- verifying the resulting service with `podman ps -a` and `systemctl`

**Setup summary:**

1. Copy `containers/nginx-container/dropins/` into your site repo under `containers/<vmid>/`
2. Create the required container secret: `proxnix-secrets set <vmid> nginx_index_message`
3. Publish config and secrets: `proxnix-publish --vmid <vmid>`
4. Restart the container: `pct restart <vmid>`
5. Verify inside the guest: `systemctl status podman-nginx.service && curl http://127.0.0.1:8080/`
