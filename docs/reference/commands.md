# Command Reference

## Host commands

### `./install.sh`

Install proxnix onto the current Proxmox node.

Useful flags:

| Flag | Purpose |
|------|---------|
| `--dry-run` | Preview what would be installed without writing anything |
| `--force-shared` | Deprecated compatibility flag; ignored in node-local mode |

### `ansible/install.yml`

Install proxnix onto one or more Proxmox nodes through Ansible.

```bash
ansible-playbook -i inventory.ini ansible/install.yml
ansible-playbook -i inventory.ini ansible/install.yml -e proxnix_target_hosts=pve_nodes
```

### `remote/codeberg-install.sh`

Curl-friendly wrapper for `install.sh`.

```bash
bash -c "$(curl -fsSL https://codeberg.org/<owner>/<repo>/raw/branch/main/remote/codeberg-install.sh)"
bash -c "$(curl -fsSL https://codeberg.org/<owner>/<repo>/raw/branch/main/remote/codeberg-install.sh)" -- --dry-run
```

### `./uninstall.sh`

Remove proxnix's installed assets from the current Proxmox node. Leaves `/var/lib/proxnix` intact.

### `proxnix-doctor <vmid>`

Run host and per-container health checks.

```bash
proxnix-doctor 100
proxnix-doctor --all
proxnix-doctor --host-only
```

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | Warnings found, no hard failures |
| 2 | One or more hard failures |

Sample output for a healthy relay-backed container:

```text
[ct 100]
  OK    PVE config present: /etc/pve/lxc/100.conf
  OK    ostype=nixos
  INFO  state: running
  OK    guest file present: /etc/nixos/configuration.nix
  OK    host relay container age identity present: /var/lib/proxnix/private/containers/100/age_identity.txt
  OK    guest container age identity present
  OK    applied managed config hash matches current hash
```

### `proxnix-create-lxc`

Create a NixOS LXC on a Proxmox host that is ready for proxnix management.

This helper:

- checks the existing proxnix install by calling `proxnix-doctor --host-only`
- auto-detects the newest local NixOS template when `--template` is omitted
- auto-detects a rootdir-capable storage when `--storage` is omitted
- creates the CT with `ostype=nixos`
- always enables `features: nesting=1`
- starts the CT by default after creating it
- optionally creates `/var/lib/proxnix/containers/<vmid>/{quadlets,dropins}`
- never attempts to install proxnix itself
- does not generate secret identities on the host

## Workstation commands

### `proxnix-secrets`

This is the workstation-authoritative helper for the external proxnix site repo.

**Configuration:** `~/.config/proxnix/config` (see [installation step 3](../getting-started/installation.md#step-3-configure-your-workstation))

### Listing

```bash
proxnix-secrets ls
proxnix-secrets ls <vmid>
proxnix-secrets ls-shared
```

### Reading

```bash
proxnix-secrets get <vmid> <name>
proxnix-secrets get-shared <name>
```

### Writing

```bash
proxnix-secrets set <vmid> <name>
proxnix-secrets set-shared <name>
```

Both commands prompt interactively for the secret value. You can also pipe a value:

```bash
printf %s "myvalue" | proxnix-secrets set 120 db_password
```

### Removing

```bash
proxnix-secrets rm <vmid> <name>
proxnix-secrets rm-shared <name>
```

### Rotating recipients

```bash
proxnix-secrets rotate <vmid>
proxnix-secrets rotate-shared
```

### Identity initialization

```bash
proxnix-secrets init-shared
proxnix-secrets init-container 120
```

`set` and `set-shared` create identities automatically when needed, so the explicit init commands are optional.

### `proxnix-publish`

Publish the workstation-owned site repo to one or more Proxmox relay hosts.

```bash
proxnix-publish
proxnix-publish root@node1
proxnix-publish --dry-run
```

It pushes config, encrypted secret stores, and decrypted relay identities into `/var/lib/proxnix` on the target hosts.

## Guest commands

### `proxnix-help`

Print a short live summary inside the guest, including VMID, IP, memory, disk, config status, and useful follow-up commands.

### `proxnix-secrets ls`

List visible secret names and whether they come from the shared or container store.

### `proxnix-secrets get <name>`

Read a decrypted secret value from the guest. Checks the container store first, then the shared store.

### `proxnix-secrets get-shared <name>`

Read a secret only from the shared store.

### Useful Podman commands

```bash
podman ps -a
podman logs -f <name>
podman auto-update --dry-run
systemctl status podman-<name>.service
```

### Useful NixOS commands

```bash
nixos-rebuild switch
nixos-rebuild list-generations
nix-collect-garbage -d
```

### Quadlet config tracking

```bash
jj -R /etc/proxnix/quadlets status
jj -R /etc/proxnix/quadlets diff
```
