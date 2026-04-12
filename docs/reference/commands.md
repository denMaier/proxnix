# Command Reference

## Host commands

### `./install.sh`

Install proxnix onto the current Proxmox node.

Useful flags:

| Flag | Purpose |
|------|---------|
| `--dry-run` | Preview what would be installed without writing anything |
| `--force-shared` | Overwrite shared pmxcfs content even if it already exists |

### `remote/codeberg-install.sh`

Curl-friendly wrapper for `install.sh`. It downloads a repo archive from Codeberg into a temporary directory and then executes the real `install.sh` from that checkout.

Typical usage after publishing your own repo copy:

```bash
bash -c "$(curl -fsSL https://codeberg.org/<owner>/<repo>/raw/branch/main/remote/codeberg-install.sh)"

bash -c "$(curl -fsSL https://codeberg.org/<owner>/<repo>/raw/branch/main/remote/codeberg-install.sh)" -- --dry-run
```

You can also override the archive source without editing the wrapper:

```bash
PROXNIX_REPO_ARCHIVE_URL=https://codeberg.org/<owner>/<repo>/archive/main.tar.gz \
  bash -c "$(curl -fsSL https://codeberg.org/<owner>/<repo>/raw/branch/main/remote/codeberg-install.sh)"
```

### `./uninstall.sh`

Remove proxnix's per-node assets from the current Proxmox node. Leaves shared cluster data intact.

Useful flag:

| Flag | Purpose |
|------|---------|
| `--dry-run` | Preview what would be removed |

### `./bootstrap-guest-secrets.sh <vmid>`

Read the guest's generated SSH public key used as an `age` recipient and store it under `/etc/pve/proxnix/containers/<vmid>/age_pubkey`.

**Prerequisites:** The container must have booted at least once with `base.nix` applied (i.e., after running `proxnix-bootstrap.sh` inside the guest).

### `proxnix-doctor <vmid>`

Run host and per-container health checks.

```bash
# Check a single container
proxnix-doctor 100

# Check all known containers
proxnix-doctor --all

# Check multiple specific containers
proxnix-doctor 100 101 102

# Check only the local proxnix installation on this node
proxnix-doctor --host-only
```

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | Warnings found, no hard failures |
| 2 | One or more hard failures |

`--host-only` is useful for automation that needs to verify a node has the proxnix hooks, helpers, and shared files in place before creating or starting a container.

### `proxnix-create-lxc`

Create a NixOS LXC on a Proxmox host that is ready for proxnix management.

This helper:

- checks the existing proxnix install by calling `proxnix-doctor --host-only`
- auto-detects the newest local NixOS template when `--template` is omitted
- auto-detects a rootdir-capable storage when `--storage` is omitted
- creates the CT with `ostype=nixos`
- optionally enables `features: nesting=1`
- optionally creates `/etc/pve/proxnix/containers/<vmid>/{quadlets,dropins}`
- never attempts to install proxnix itself

Example:

```bash
proxnix-create-lxc \
  --vmid 120 \
  --hostname nixos-media \
  --disk 16 \
  --memory 4096 \
  --cores 4 \
  --nesting \
  --start
```

After running `install.sh` once on a node, use the installed local helper directly:

```bash
/usr/local/sbin/proxnix-create-lxc \
  --vmid 120 \
  --hostname nixos-media \
  --disk 16 \
  --memory 4096 \
  --cores 4 \
  --nesting \
  --start
```

That helper is localized by `install.sh`, so creating additional containers does not require re-downloading the repository.
Pass `--template ...` or `--storage ...` only when you want to override the auto-detected defaults.

Sample output for `proxnix-doctor`:

```text
[host]
  OK    /usr/share/lxc/config/nixos.common.conf present
  OK    /usr/share/lxc/config/nixos.userns.conf present
  OK    /usr/share/lxc/hooks/nixos-proxnix-prestart present
  OK    /usr/share/lxc/hooks/nixos-proxnix-mount present
  OK    /usr/local/lib/proxnix/yaml-to-nix.py present
  OK    /usr/local/lib/proxnix/nixos-proxnix-common.sh present
  OK    /usr/local/lib/proxnix/proxnix-secrets-guest present
  OK    /usr/local/sbin/proxnix-doctor present
  OK    /etc/pve/proxnix/base.nix present
  OK    /etc/pve/proxnix/common.nix present
  OK    /etc/pve/proxnix/configuration.nix present
  OK    /etc/pve/priv/proxnix present

[ct 100]
  OK    PVE config present: /etc/pve/lxc/100.conf
  OK    ostype=nixos
  INFO  workload mode: native services
  INFO  state: running
  OK    guest file present: /etc/nixos/configuration.nix
  OK    guest file present: /etc/nixos/managed/base.nix
  OK    applied managed config hash matches current hash
  OK    bootstrap marker present

Summary: 0 fail(s), 0 warning(s)
```

## `proxnix-secrets` (host/workstation)

This is the host-side admin helper for SOPS-backed proxnix secret stores.

**Configuration:** `~/.config/proxnix/config` (see [installation step 5](../getting-started/installation.md#step-5-configure-your-workstation))

### Listing

```bash
proxnix-secrets ls                # all secrets across all containers and shared
proxnix-secrets ls <vmid>         # secrets visible to a specific container (container + shared)
proxnix-secrets ls-shared         # only shared secrets
```

### Reading

```bash
proxnix-secrets get <vmid> <name>       # decrypt from container store (falls back to shared)
proxnix-secrets get-shared <name>       # decrypt from shared store only
```

### Writing

```bash
proxnix-secrets set <vmid> <name>       # create or update a per-container secret
proxnix-secrets set-shared <name>       # create or update a shared secret
```

Both commands prompt interactively for the secret value (with confirmation). You can also pipe a value:

```bash
printf %s "myvalue" | proxnix-secrets set <vmid> <name>
```

### Removing

```bash
proxnix-secrets rm <vmid> <name>        # remove from container store
proxnix-secrets rm-shared <name>        # remove from shared store
```

### Rotating recipients

```bash
proxnix-secrets rotate <vmid>           # re-encrypt container store to current recipients
proxnix-secrets rotate-shared           # re-encrypt shared store to current recipients
```

### Shared key initialization

```bash
proxnix-secrets init-shared             # generate shared age keypair (run once)
```

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
podman ps -a                                    # list all containers
podman logs -f <name>                           # follow container logs
podman auto-update --dry-run                    # check for image updates
systemctl status podman-<name>.service          # check systemd unit status
```

### Useful NixOS commands

```bash
nixos-rebuild switch                            # manually apply config changes
nixos-rebuild list-generations                  # list config generations
nix-collect-garbage -d                          # free disk space
```

### Quadlet config tracking

```bash
jj -R /etc/proxnix/quadlets status             # check for host-managed config changes
jj -R /etc/proxnix/quadlets diff               # see what changed
```
