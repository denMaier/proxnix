# Day-2 Operations

This page covers normal operational tasks after initial bootstrap.

> **Key principle:** proxnix stages config only at container startup. Every host-side change requires a container restart to take effect. There is no live-reload mechanism — this is by design.

## Change networking or SSH keys

Use the Proxmox WebUI for the primary container definition.

After making a change, restart the CT:

```bash
pct restart <vmid>
```

For host-managed config outside the Proxmox CT definition, update `dropins/*.nix` and restart.

## Change native service config

Edit one of these on the host:

- `dropins/*.nix`
- `dropins/*.{sh,py}`

Then restart the container so proxnix re-stages and syncs the managed tree:

```bash
pct restart <vmid>
```

Watch the rebuild:

```bash
pct exec <vmid> -- journalctl -u proxnix-apply-config.service -b -f
```

## Change container workloads

Edit guest Nix workload files under:

- `dropins/*.nix`
- optional supporting `dropins/*.{sh,py}`

Then restart the container.

Inside the guest, useful commands may include:

```bash
podman ps -a
systemctl status podman-<name>.service
```

## Experimenting in the guest before committing

The most efficient workflow for complex changes is to experiment in the guest first:

1. **Enter the guest**: `pct enter <vmid>`
2. **Edit `local.nix`**: Add your new configuration to `/etc/nixos/local.nix`.
3. **Apply changes**: Run `nixos-rebuild switch`.
4. **Repeat**: Tweak and apply until the configuration is correct.
5. **Commit to host**: Move the final configuration from `local.nix` into host-side `dropins/*.nix`.
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
  OK    host relay encrypted container identity present: /var/lib/proxnix/private/containers/100/age_identity.sops.yaml

Summary: 0 fail(s), 0 warning(s)
```

## Secret management

From the workstation:

```bash
# Per-container secrets
proxnix-secrets ls                   # list all secrets across all containers
proxnix-secrets ls <vmid>            # list secrets visible to a specific container
proxnix-secrets get <vmid> <name>    # read a decrypted secret value
proxnix-secrets set <vmid> <name>    # create or update a secret
proxnix-secrets rm <vmid> <name>     # remove a secret
proxnix-secrets rotate <vmid>        # re-encrypt to configured recipients

# Shared secrets
proxnix-secrets ls-shared
proxnix-secrets set-shared <name>
proxnix-secrets rm-shared <name>
proxnix-secrets rotate-shared
```

After changing secrets, publish and restart:

```bash
proxnix-publish
pct restart <vmid>
```

## Updating proxnix itself

Typical sequence:

1. Build or fetch the latest `proxnix-host` package
2. Install it on every node that should host proxnix-managed containers
3. If you are still on the shell-installer path, run `host/install.sh` instead
4. Restart managed containers as needed

Once a node is installed, it does not need to retain that repo checkout for
normal operations. Package-installed nodes use `apt remove proxnix-host`; nodes
installed with the shell installer use `proxnix-uninstall`.

## Updating the admin password

The admin password hash is a shared secret. To change it:

```bash
mkpasswd -m sha-512
proxnix-secrets set-shared common_admin_password_hash
# Paste the new hash when prompted
```

Then restart each container so the updated secret is staged on next boot.

## Stage directory cleanup

proxnix installs a timer that periodically removes stale directories under `/run/proxnix/` for containers that are no longer running.
