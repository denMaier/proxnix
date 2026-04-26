# Troubleshooting

## General debugging approach

Most proxnix problems can be diagnosed by checking these in order:

1. **Host hooks:** Did the pre-start and mount hooks run?
   ```bash
   journalctl -t proxnix-prestart -t proxnix-mount -t proxnix-poststop -n 100
   journalctl -t lxc-<vmid>-start -n 50   # raw LXC hook output, also useful with pct start <vmid> --debug
   ```

2. **Guest boot activation:** Did the seeded closure activate?
   ```bash
   pct exec <vmid> -- journalctl -u proxnix-boot-activate.service -b
   ```

3. **Reconciler status:** Does the recorded desired system match the guest?
   ```bash
   proxnix-reconcile --status --vmid <vmid>
   pct exec <vmid> -- readlink -f /run/current-system
   ```

4. **Doctor:** Run the full health check:
   ```bash
   proxnix-doctor <vmid>
   ```

---

## Boot activation does not finish

Check the boot activation service log:

```bash
pct exec <vmid> -- journalctl -u proxnix-boot-activate.service -b
```

If you need to debug manually inside the guest, inspect the host-rendered build
input snapshot:

```bash
pct enter <vmid>
nixos-rebuild test -I nixos-config=/var/lib/proxnix/build-input/configuration.nix
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
   pct exec <vmid> -- systemctl status proxnix-secret-oneshot-proxnix-common-admin-password.service
   pct exec <vmid> -- journalctl -u proxnix-secret-oneshot-proxnix-common-admin-password.service -b
   ```

## Container workloads do not start correctly

Check the guest Nix workload first.

From the host:

```bash
proxnix-doctor <vmid>
```

Then inspect the guest:

```bash
pct exec <vmid> -- journalctl -b
pct exec <vmid> -- systemctl --failed
```

If a proxnix-managed NixOS CT is missing `nesting=1,keyctl=1`, correct the CT
features in Proxmox and restart the container.

## Secrets cannot be encrypted for a container

`proxnix-secrets set <vmid> ...` needs access to the workstation site repo and, for `embedded-sops`, the configured SOPS master identity.

Check:

```bash
ls "$PROXNIX_SITE_DIR/private/containers/<vmid>/age_identity.sops.yaml"
ls "$PROXNIX_SOPS_MASTER_IDENTITY"
```

If the encrypted identity is missing, `proxnix-secrets set <vmid> ...` will create it automatically.

## `proxnix-publish` cannot reach a host

Check the workstation config:

```bash
mkdir -p ~/.config/proxnix
cat > ~/.config/proxnix/config << 'EOF'
PROXNIX_SITE_DIR=~/src/proxnix-site
PROXNIX_HOSTS="root@your-proxmox-host"
PROXNIX_SSH_IDENTITY=~/.ssh/id_ed25519
PROXNIX_SOPS_MASTER_IDENTITY=~/.ssh/proxnix-master
EOF
```

See [installation step 3](../getting-started/installation.md#step-3-configure-your-workstation).

## `proxnix-secrets` says "PROXNIX_SITE_DIR not set"

The workstation config file is missing. Create it:

```bash
mkdir -p ~/.config/proxnix
cat > ~/.config/proxnix/config << 'EOF'
PROXNIX_SITE_DIR=~/src/proxnix-site
PROXNIX_HOSTS="root@your-proxmox-host"
PROXNIX_SSH_IDENTITY=~/.ssh/id_ed25519
PROXNIX_SOPS_MASTER_IDENTITY=~/.ssh/proxnix-master
EOF
```

See [installation step 3](../getting-started/installation.md#step-3-configure-your-workstation).

## `proxnix-secrets` says "SOPS master SSH identity not found"

The identity file specified in `PROXNIX_SOPS_MASTER_IDENTITY` doesn't exist. Check the path in `~/.config/proxnix/config`.

Default location:

```bash
PROXNIX_SOPS_MASTER_IDENTITY=~/.ssh/proxnix-master
```

## A native service cannot read its secret file

Check three things:

1. The secret is present in the host store:
   ```bash
   proxnix-secrets ls <vmid>
   ```

2. The service declared it in a host-side `dropins/*.nix` module:
   ```nix
   {
     proxnix.secrets.the_secret = {
       source = {
         scope = "container";
         name = "the_secret";
       };
       file = {
         owner = "root";
         group = "myservice";
         mode = "0640";
         restartUnits = [ "myservice.service" ];
       };
     };
   }
   ```

3. The service configuration actually points at the generated path

Remember that proxnix only extracts the secret file. The service still needs to consume that path.

## A host-side change did not appear in the guest

Restart the CT. proxnix stages and syncs its managed files during container startup, not continuously while the container is already running.

```bash
pct restart <vmid>
```

This is the expected behavior, not a bug. See [day-2 operations](day-2.md).

## The guest still uses old config after restart

From the host, compare:

```bash
proxnix-reconcile --status --vmid <vmid>
pct exec <vmid> -- readlink -f /run/current-system
```

If they differ, inspect activation:

```bash
pct exec <vmid> -- systemctl status proxnix-boot-activate.service
pct exec <vmid> -- journalctl -u proxnix-boot-activate.service -b
```

Common causes:

- The host build failed before seeding
- Offline closure seeding failed in the mount hook
- Boot activation failed and reverted to `previous-system`

## The hooks seem broken on one node

Rerun the Ansible host deployment for that node:

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml
```

The hooks and helper binaries are per-node assets. If a node was reinstalled or upgraded, the local files may be missing.
The original repo checkout is not required on the Proxmox node after a successful install.

## Container migration to another node

After migrating a container to a different Proxmox node, make sure proxnix is installed on that node:

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml
```

proxnix keeps its host-side data under `/var/lib/proxnix/`. If you migrate a container to another node, make sure that node has both proxnix installed and the expected `/var/lib/proxnix/` data for that container.

## `pve-conf-to-nix.py` fails

Check the pre-start hook log:

```bash
journalctl -t lxc-<vmid>-start -n 50
```

Common causes:

- Missing PVE config file for the VMID
- Python3 not installed on the Proxmox host

## Need a broad sanity check

Run:

```bash
proxnix-doctor --all
```

This checks the host installation and every known container.
