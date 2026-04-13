# Troubleshooting

## General debugging approach

Most proxnix problems can be diagnosed by checking these in order:

1. **Host hooks:** Did the pre-start and mount hooks run?
   ```bash
   journalctl -t lxc-<vmid>-start -n 50   # or check syslog
   ```

2. **Guest apply service:** Did the config get applied?
   ```bash
   pct exec <vmid> -- journalctl -u proxnix-apply-config.service -b
   ```

3. **Config hash:** Are the hashes in sync?
   ```bash
   pct exec <vmid> -- cat /etc/proxnix/current-config-hash
   pct exec <vmid> -- cat /etc/proxnix/applied-config-hash
   ```

4. **Doctor:** Run the full health check:
   ```bash
   proxnix-doctor <vmid>
   ```

---

## Automatic first rebuild does not finish

Check the first-boot apply service log:

```bash
pct exec <vmid> -- journalctl -u proxnix-apply-config.service -b
```

If you need to retry manually inside the guest:

```bash
pct enter <vmid>
/root/proxnix-bootstrap.sh
```

## First rebuild fails during automatic bootstrap

Check the CT memory allocation. Nix evaluation needs at least **2 GB RAM** for the initial `nixos-rebuild switch`.

Increase memory in the Proxmox WebUI, then restart and re-run the recovery helper.

## Admin user cannot use `sudo`

If `sudo` asks for a password and you get "Authentication failure", the admin password hash secret is either missing or not yet applied.

Check:

1. Is the shared secret set?
   ```bash
   proxnix-secrets ls-shared
   # Should show: common_admin_password_hash
   ```

2. If missing, set it:
   ```bash
   mkpasswd -m sha-512
   proxnix-secrets set-shared common_admin_password_hash
   pct restart <vmid>
   ```

3. Check the service inside the guest:
   ```bash
   pct exec <vmid> -- systemctl status proxnix-common-admin-password.service
   pct exec <vmid> -- journalctl -u proxnix-common-admin-password.service -b
   ```

## Quadlet workloads do not start correctly

Check the Proxmox CT features and make sure `nesting=1` is enabled.

From the host:

```bash
pct config <vmid>
proxnix-doctor <vmid>
```

If nesting is not set:

```bash
pct set <vmid> --features nesting=1
pct restart <vmid>
```

## Secrets cannot be encrypted for a container

`proxnix-secrets set <vmid> ...` needs access to the workstation site repo and the master identity.

Check:

```bash
ls "$PROXNIX_SITE_DIR/private/containers/<vmid>/age_identity.sops.json"
ls "$PROXNIX_MASTER_IDENTITY"
```

If the encrypted identity is missing, `proxnix-secrets set <vmid> ...` will create it automatically.

## `proxnix-publish` cannot reach a host

Check the workstation config:

```bash
mkdir -p ~/.config/proxnix
cat > ~/.config/proxnix/config << 'EOF'
PROXNIX_SITE_DIR=~/src/proxnix-site
PROXNIX_MASTER_IDENTITY=~/.ssh/proxnix-master
PROXNIX_HOSTS="root@your-proxmox-host"
PROXNIX_SSH_IDENTITY=~/.ssh/id_ed25519
EOF
```

See [installation step 3](../getting-started/installation.md#step-3-configure-your-workstation).

## `proxnix-secrets` says "PROXNIX_SITE_DIR not set"

The workstation config file is missing. Create it:

```bash
mkdir -p ~/.config/proxnix
cat > ~/.config/proxnix/config << 'EOF'
PROXNIX_SITE_DIR=~/src/proxnix-site
PROXNIX_MASTER_IDENTITY=~/.ssh/proxnix-master
PROXNIX_HOSTS="root@your-proxmox-host"
PROXNIX_SSH_IDENTITY=~/.ssh/id_ed25519
EOF
```

See [installation step 3](../getting-started/installation.md#step-3-configure-your-workstation).

## `proxnix-secrets` says "master SSH identity not found"

The identity file specified in `PROXNIX_MASTER_IDENTITY` doesn't exist. Check the path in `~/.config/proxnix/config`.

Default location:

```bash
PROXNIX_MASTER_IDENTITY=~/.ssh/proxnix-master
```

## A native service cannot read its secret file

Check three things:

1. The secret is present in the host store:
   ```bash
   proxnix-secrets ls <vmid>
   ```

2. The service declared it under `user.yaml`:
   ```yaml
   services:
     myservice:
       secrets:
         - name: the_secret
           path: /run/myservice-secrets/the_secret
   ```

3. The service configuration actually points at the generated `/run/...` path

Remember that proxnix only extracts the secret file. The service still needs to consume that path.

## A host-side change did not appear in the guest

Restart the CT. proxnix stages and syncs its managed files during container startup, not continuously while the container is already running.

```bash
pct restart <vmid>
```

This is the expected behavior, not a bug. See [day-2 operations](day-2.md).

## The guest still uses old config after restart

Inside the guest, compare:

```bash
cat /etc/proxnix/current-config-hash
cat /etc/proxnix/applied-config-hash
```

If they differ, inspect the generated service:

```bash
systemctl status proxnix-apply-config.service
journalctl -u proxnix-apply-config.service -b
```

Common causes:

- The rebuild failed (check the journal for Nix evaluation errors)
- Not enough RAM to complete the rebuild
- Network issues preventing Nix from fetching packages

## The hooks seem broken on one node

Run the installer again on that node:

```bash
./install.sh
```

The hooks and helper binaries are per-node assets. If a node was reinstalled or upgraded, the local files may be missing.

## Container migration to another node

After migrating a container to a different Proxmox node, make sure proxnix is installed on that node:

```bash
./install.sh
```

proxnix keeps its host-side data under `/var/lib/proxnix/`. If you migrate a container to another node, make sure that node has both proxnix installed and the expected `/var/lib/proxnix/` data for that container.

## `yaml-to-nix.py` fails

Check the pre-start hook log:

```bash
journalctl -t lxc-<vmid>-start -n 50
```

Common causes:

- Malformed YAML in `proxmox.yaml` or `user.yaml`
- Missing PVE config file for the VMID
- Python3 not installed on the Proxmox host

## Need a broad sanity check

Run:

```bash
proxnix-doctor --all
```

This checks the host installation and every known container.
