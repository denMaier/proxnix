# Installation

This page covers installing proxnix on Proxmox cluster nodes, preparing the shared configuration and secrets directories, and setting up your workstation for secret management.

## Checklist

Use this checklist to make sure you don't miss anything. Each item is explained in detail below.

- [ ] Run `./install.sh` on every Proxmox node
- [ ] Store the master age recovery key
- [ ] Initialize the shared age keypair (if using shared secrets)
- [ ] Set the admin user password hash as a shared secret
- [ ] Configure your workstation for `proxnix-secrets`

## What `install.sh` does

Run `install.sh` on every Proxmox node that may start proxnix-managed containers.

It installs two kinds of assets:

### Per-node files

These are installed locally on each node because the LXC hooks execute on that node at container startup time.

- `/usr/share/lxc/config/nixos.common.conf`
- `/usr/share/lxc/config/nixos.userns.conf`
- `/usr/share/lxc/hooks/nixos-proxnix-prestart`
- `/usr/share/lxc/hooks/nixos-proxnix-mount`
- `/usr/local/lib/proxnix/yaml-to-nix.py`
- `/usr/local/lib/proxnix/nixos-proxnix-common.sh`
- `/usr/local/lib/proxnix/proxnix-secrets-guest`
- `/usr/local/sbin/proxnix-doctor`
- `proxnix-gc.service` and `proxnix-gc.timer`

### Shared cluster files

These live under `pmxcfs`, so they replicate across the cluster.

- `/etc/pve/proxnix/base.nix`
- `/etc/pve/proxnix/common.nix`
- `/etc/pve/proxnix/configuration.nix`
- `/etc/pve/proxnix/containers/`
- `/etc/pve/priv/proxnix/shared/`
- `/etc/pve/priv/proxnix/containers/`

## Step 1: Install on the Proxmox host

Clone the repo and run the installer as root:

```bash
git clone <this repo>
cd proxnix
./install.sh
```

On additional cluster nodes, run the same command. The installer skips recreating the shared tree unless you explicitly force it.

Use `--dry-run` to preview what would be installed without writing anything.

## Step 2: Store the master age recovery key

**Why:** The master key is included as an encryption recipient for every secret store (both per-container and shared). Without it, you cannot recover secrets if a container's age identity is lost.

**When to skip:** Never. Always set up the master key.

```bash
ssh-keygen -y -f ~/.ssh/id_ed25519 > /etc/pve/proxnix/master_age_pubkey
```

You can use an existing SSH ed25519 key because `age` natively supports SSH recipients. If you prefer a dedicated age key, generate one with `age-keygen` and store the public key instead.

> **⚠️ Keep the corresponding private key safe.** If you lose the master private key, you lose the ability to decrypt any secret store that was encrypted to it.

## Step 3: Initialize the shared age keypair

**Why:** Shared secrets are available in every container. They're useful for credentials that multiple services need, like the admin user password hash.

**When to skip:** Only if you are sure no container will ever need a shared secret. In practice, you almost always want this because the admin password hash uses it.

```bash
proxnix-secrets init-shared
```

That creates:

- `/etc/pve/priv/proxnix/shared_age_identity.txt` — private key, staged into every guest
- `/etc/pve/proxnix/shared_age_pubkey` — public key, used as encryption recipient

## Step 4: Set the admin user password hash

**Why:** Every proxnix-managed container creates a shared `admin` user (configurable in `common.nix`). By default, this user is SSH-key-only with a locked password. If you want the admin user to have a password (for `sudo`, console login, etc.), you must store a shadow-compatible password hash as a shared secret.

The default configuration in `base.nix` reads the password hash from a shared secret named `common_admin_password_hash`.

### Generate the hash

```bash
# Interactive — prompts for password, outputs the hash
mkpasswd -m sha-512
```

Or non-interactively:

```bash
mkpasswd -m sha-512 "your-password-here"
```

### Store it as a shared secret

```bash
proxnix-secrets set-shared common_admin_password_hash
```

Paste the full `$6$...` hash when prompted.

> **⚠️ Do not skip this if `wheelNeedsPassword = true`.** The default `base.nix` sets `wheelNeedsPassword = true`, which means `sudo` requires a password. If you don't set the password hash, the admin user will be locked out of `sudo`.

### What happens inside the guest

On boot, the `proxnix-common-admin-password` systemd service:

1. Reads the `common_admin_password_hash` secret from the SOPS store
2. Applies it to the admin user via `chpasswd -e`

If the secret is not yet available (e.g., before the first secret bootstrap), the service logs a message and skips — it does not fail.

## Step 5: Configure your workstation

The `proxnix-secrets` command runs from your workstation (or any machine with SSH access to the Proxmox host). It needs to know how to reach the host and which age identity to use for SOPS operations.

### Create the config file

```bash
mkdir -p ~/.config/proxnix
cat > ~/.config/proxnix/config << 'EOF'
PROXNIX_HOST=root@your-proxmox-host
PROXNIX_IDENTITY=~/.ssh/id_ed25519
EOF
```

| Variable | Purpose | Default |
|----------|---------|---------|
| `PROXNIX_HOST` | SSH target for the Proxmox host | *(required)* |
| `PROXNIX_IDENTITY` | Path to your local age or SSH private key used by SOPS | `~/.config/age/identity.txt` |
| `PROXNIX_DIR` | Shared config dir on the Proxmox host | `/etc/pve/proxnix` |
| `PROXNIX_PRIV_DIR` | Private secret dir on the Proxmox host | `/etc/pve/priv/proxnix` |

### Required workstation tools

Make sure these are available in your `$PATH`:

- `ssh`
- `sops`
- `age-keygen`
- `python3`

### Verify it works

```bash
proxnix-secrets ls
```

This should show an empty list or the `common_admin_password_hash` secret you created earlier.

## Upgrading shared proxnix files

If the repo changes `base.nix`, `common.nix`, or `configuration.nix`, push the updated shared copies with:

```bash
./install.sh --force-shared
```

Run that on one node. Then run plain `./install.sh` on the remaining nodes so their local hooks and helpers stay in sync.

After upgrading, restart managed containers so they pick up the new config.

## Uninstalling

To remove proxnix from a node but keep the shared cluster data:

```bash
./uninstall.sh
```

This removes only the per-node assets. It intentionally leaves `/etc/pve/proxnix` and `/etc/pve/priv/proxnix` alone.

## What you should have when done

After completing all steps, your setup should look like this:

```
/etc/pve/proxnix/
├── base.nix                    ← shared NixOS baseline
├── common.nix                  ← shared operator module
├── configuration.nix           ← NixOS entrypoint
├── master_age_pubkey           ← your recovery key (step 2)
├── shared_age_pubkey           ← shared encryption recipient (step 3)
├── containers/                 ← per-container config (populated later)
│
/etc/pve/priv/proxnix/
├── shared_age_identity.txt     ← shared private key (step 3)
├── shared/
│   └── secrets.sops.yaml       ← shared secrets including admin hash (step 4)
├── containers/                 ← per-container secrets (populated later)

~/.config/proxnix/
└── config                      ← workstation config (step 5)
```

Proceed to [first container](first-container.md) to onboard your first NixOS LXC.
