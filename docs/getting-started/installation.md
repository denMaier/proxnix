# Installation

This page covers installing proxnix on a Proxmox node, preparing the node-local configuration and secrets directories, and setting up your workstation for secret management.

## Checklist

Use this checklist to make sure you don't miss anything. Each item is explained in detail below.

- [ ] Run `./install.sh` on every Proxmox node
- [ ] Store the master SSH-backed age recovery key
- [ ] Initialize the shared SSH-backed age keypair (if using shared secrets)
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

### Node-local proxnix files

These live on the local node under `/var/lib/proxnix/`. If you want the same proxnix data on multiple nodes, keep these directories in sync yourself.

- `/var/lib/proxnix/base.nix`
- `/var/lib/proxnix/common.nix`
- `/var/lib/proxnix/configuration.nix`
- `/var/lib/proxnix/containers/`
- `/var/lib/proxnix/private/shared/`
- `/var/lib/proxnix/private/containers/`

## Step 1: Install on the Proxmox host

Clone the repo and run the installer as root:

```bash
git clone <this repo>
cd proxnix
./install.sh
```

Run the installer on every node that should host proxnix-managed containers.

Use `--dry-run` to preview what would be installed without writing anything.

## Step 2: Store the master SSH-backed age recovery key

**Why:** The master key is included as an encryption recipient for every secret store (both per-container and shared). Without it, you cannot recover secrets if a container's SSH-backed age identity is lost.

**When to skip:** Never. Always set up the master key.

```bash
ssh-keygen -y -f ~/.ssh/id_ed25519 > /var/lib/proxnix/master_age_pubkey
```

Use an SSH ed25519 key here. proxnix standardizes on SSH keys used as `age` recipients so SOPS only needs one identity mode end to end.

> **⚠️ Keep the corresponding private key safe.** If you lose the master private key, you lose the ability to decrypt any secret store that was encrypted to it.

## Step 3: Initialize the shared SSH-backed age keypair

**Why:** Shared secrets are available in every container. They're useful for credentials that multiple services need, like the admin user password hash.

**When to skip:** Only if you are sure no container will ever need a shared secret. In practice, you almost always want this because the admin password hash uses it.

```bash
proxnix-secrets init-shared
```

That creates:

- `/var/lib/proxnix/private/shared_age_identity.txt` — SSH private key, staged into every guest
- `/var/lib/proxnix/shared_age_pubkey` — public key, used as encryption recipient

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

The `proxnix-secrets` command runs from your workstation (or any machine with SSH access to the Proxmox host). It needs to know how to reach the host and which SSH private key to use for SOPS operations.

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
| `PROXNIX_IDENTITY` | Path to your local SSH private key used by SOPS | `~/.ssh/id_ed25519` |
| `PROXNIX_DIR` | Node-local proxnix config dir on the Proxmox host | `/var/lib/proxnix` |
| `PROXNIX_PRIV_DIR` | Node-local private proxnix dir on the Proxmox host | `/var/lib/proxnix/private` |

### Required workstation tools

Make sure these are available in your `$PATH`:

- `ssh`
- `ssh-keygen`
- `sops`
- `python3`

### Verify it works

```bash
proxnix-secrets ls
```

This should show an empty list or the `common_admin_password_hash` secret you created earlier.

## Upgrading proxnix files

If the repo changes `base.nix`, `common.nix`, or `configuration.nix`, reinstall proxnix on each node that should host proxnix-managed containers:

```bash
./install.sh
```

The `--force-shared` flag is kept only as a deprecated compatibility no-op in node-local mode.

After upgrading, restart managed containers so they pick up the new config.

## Uninstalling

To remove proxnix from a node but keep the node-local config data:

```bash
./uninstall.sh
```

This removes only the installed hooks, helpers, and timers. It intentionally leaves `/var/lib/proxnix` alone.

## What you should have when done

After completing all steps, your setup should look like this:

```
/var/lib/proxnix/
├── base.nix                    ← shared NixOS baseline
├── common.nix                  ← shared operator module
├── configuration.nix           ← NixOS entrypoint
├── site.nix                    ← optional site override from your data repo
├── master_age_pubkey           ← your recovery key (step 2)
├── shared_age_pubkey           ← shared encryption recipient (step 3)
├── containers/                 ← per-container config (populated later)
│
/var/lib/proxnix/private/
├── shared_age_identity.txt     ← shared SSH private key used as an age identity (step 3)
├── shared/
│   └── secrets.sops.yaml       ← shared secrets including admin hash (step 4)
├── containers/                 ← per-container secrets (populated later)

~/.config/proxnix/
└── config                      ← workstation config (step 5)
```

Proceed to [first container](first-container.md) to onboard your first NixOS LXC.
