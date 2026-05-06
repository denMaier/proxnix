# Day-2 Operations

This page covers normal operational tasks after initial bootstrap.

> **Key principle:** proxnix builds and stages desired systems from the host. Every host-side change requires explicit host reconcile, a publish-triggered build workflow, or the daily `nix-auto` timer reconciliation to take effect. There is no live-reload mechanism — this is by design.

## Change networking or SSH keys

Use the Proxmox WebUI for the primary container definition.

After making a change, reconcile the CT:

```bash
proxnix-host reconcile --vmid <vmid>
```

For host-managed config outside the Proxmox CT definition, update
`dropins/*.nix` and reconcile.

## Change native service config

Edit one of these on the host:

- `dropins/*.nix`
- `dropins/*.{sh,py}`

Then reconcile the container so proxnix rebuilds if needed, refreshes the debug
build-input snapshot, seeds the closure, and activates it:

```bash
proxnix-host reconcile --vmid <vmid>
```

Watch the booted system:

```bash
pct exec <vmid> -- readlink -f /run/current-system
```

## Change container workloads

Edit guest Nix workload files under:

- `dropins/*.nix`
- optional supporting `dropins/*.{sh,py}`

Then reconcile the container.

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
6. **Finalize**: Reconcile the container to apply the host-side config and clear your experiments from the guest.

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
  OK    /usr/share/lxc/hooks/nixos-proxnix-start-host present
  OK    /usr/local/sbin/proxnix-host present
  ...

[ct 100]
  OK    ostype=nixos
  OK    guest file present: /var/lib/proxnix/build-input/configuration.nix
  INFO  legacy managed config hash is informational because reconciler status exists
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

After changing secrets, publish and reconcile:

```bash
proxnix-publish
proxnix-host reconcile --vmid <vmid>
```

## Updating proxnix itself

Typical sequence:

1. Rerun `host/deploy/ansible/install.yml` for every node that should host proxnix-managed containers
2. Reconcile managed containers as needed

Once a node is installed, it does not need to retain that repo checkout for
normal operations. Use `proxnix-host-uninstall` on the node to remove installed
host runtime files while keeping relay data.

For routine host cleanup, use `proxnix-host gc`. It removes stale proxnix deploy
GC roots, prunes old `/nix/var/nix/profiles/proxnix-host` generations, and runs
`nix-store --gc` after the proxnix roots are in the intended state. Avoid running
`nix-collect-garbage` directly against the Proxmox host store unless you have
first checked the proxnix deployment GC roots under
`/var/lib/proxnix/gcroots/deploy`.

## Updating the admin password

The admin password hash is a shared secret. To change it:

```bash
mkpasswd -m sha-512
proxnix-secrets set-shared common_admin_password_hash
# Paste the new hash when prompted
```

Then reconcile each container so the updated secret is staged.

## Stage directory cleanup

proxnix installs a timer that periodically removes stale directories under `/run/proxnix/` for containers that are no longer running.
