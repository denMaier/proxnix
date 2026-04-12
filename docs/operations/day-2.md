# Day-2 Operations

This page covers normal operational tasks after initial bootstrap.

> **Key principle:** proxnix stages config only at container startup. Every host-side change requires a container restart to take effect. There is no live-reload mechanism — this is by design.

## Change networking or SSH keys

Use the Proxmox WebUI for the primary container definition.

After making a change, restart the CT:

```bash
pct restart <vmid>
```

For extra SSH keys or search domains that are not modeled by Proxmox, update `proxmox.yaml` and restart.

## Change native service config

Edit one of these on the host:

- `user.yaml`
- `dropins/*.nix`
- `dropins/*.service`
- `dropins/*.{sh,py}`

Then restart the container so proxnix re-stages and syncs the managed tree:

```bash
pct restart <vmid>
```

Watch the rebuild:

```bash
pct exec <vmid> -- journalctl -u proxnix-apply-config.service -b -f
```

## Change Quadlet workloads

Edit files under:

- `quadlets/`
- Quadlet-related files under `dropins/`

Then restart the container.

Inside the guest, useful commands include:

```bash
podman ps -a
systemctl status podman-<name>.service
jj -R /etc/proxnix/quadlets status
```

## Experimenting in the guest before committing

The most efficient workflow for complex changes is to experiment in the guest first:

1. **Enter the guest**: `pct enter <vmid>`
2. **Edit `local.nix`**: Add your new configuration to `/etc/nixos/local.nix`.
3. **Apply changes**: Run `nixos-rebuild switch`.
4. **Repeat**: Tweak and apply until the configuration is correct.
5. **Commit to host**: Move the final configuration from `local.nix` into the host-side `user.yaml` or `dropins/*.nix`.
6. **Finalize**: Restart the container to apply the host-side config and clear your experiments from the guest.

## Applying temporary guest-only overrides

Use:

```text
/etc/nixos/local.nix
```

Then run `nixos-rebuild switch` inside the guest.

This file is intentionally outside the host-managed tree, so proxnix does not overwrite it.

## Health checks

From the host:

```bash
proxnix-doctor <vmid>
proxnix-doctor --all
```

Sample output for a healthy container:

```text
[host]
  OK    /usr/share/lxc/config/nixos.common.conf present
  OK    /usr/share/lxc/hooks/nixos-proxnix-prestart present
  ...

[ct 100]
  OK    ostype=nixos
  OK    applied managed config hash matches current hash
  OK    host identity marker present

Summary: 0 fail(s), 0 warning(s)
```

## Secret management

From the host (or workstation with `proxnix-secrets` configured):

```bash
# Per-container secrets
proxnix-secrets ls                   # list all secrets across all containers
proxnix-secrets ls <vmid>            # list secrets visible to a specific container
proxnix-secrets get <vmid> <name>    # read a decrypted secret value
proxnix-secrets set <vmid> <name>    # create or update a secret
proxnix-secrets rm <vmid> <name>     # remove a secret
proxnix-secrets rotate <vmid>        # re-encrypt to current recipients

# Shared secrets
proxnix-secrets ls-shared
proxnix-secrets get-shared <name>
proxnix-secrets set-shared <name>
proxnix-secrets rm-shared <name>
proxnix-secrets rotate-shared
```

> **Remember:** Restart the CT after changing secrets so the staged store and Podman secret registry are refreshed.

## Updating proxnix itself

Typical sequence:

1. Update the repo on the node where you manage proxnix
2. Run `./install.sh --force-shared` once if shared Nix files changed
3. Run `./install.sh` on every node so local hooks and helpers match the repo
4. Restart managed containers as needed

## Updating the admin password

The admin password hash is a shared secret. To change it:

```bash
mkpasswd -m sha-512
proxnix-secrets set-shared common_admin_password_hash
# Paste the new hash when prompted
```

Then restart each container. The `proxnix-common-admin-password` service will apply the new hash on boot.

## Stage directory cleanup

proxnix installs a timer that periodically removes stale directories under `/run/proxnix/` for containers that are no longer running.
