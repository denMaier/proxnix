# Installation

This page covers installing proxnix on Proxmox nodes and setting up the workstation-owned site repo that proxnix now uses as its source of truth.

## Checklist

- [ ] Install proxnix on every Proxmox node, preferably from the helper-script entrypoint that installs the `proxnix-host` Debian package, or by running `host/install.sh` locally on each node, or by running `ansible-playbook -i host/inventory.proxmox.ini host/ansible/install.yml` once from your control machine
- [ ] Create a separate workstation-owned site repo, or stop after workstation config if you only want a fresh host/bootstrap first
- [ ] Configure your workstation for `proxnix-secrets` and `proxnix-publish`
- [ ] Initialize the host relay identity
- [ ] Publish the site repo to every Proxmox node that should relay it

## What the node install does

Every Proxmox node that may start proxnix-managed containers needs the same
installed assets. The canonical path is the helper-script entrypoint, which
downloads and installs the `proxnix-host` Debian package for the local
architecture. You can also install the package manually, run `host/install.sh`
directly on the node, or use `host/ansible/install.yml` from an Ansible
control machine over SSH.

It installs two kinds of assets:

### Per-node files

These are installed locally on each node because the LXC hooks execute on that node at container startup time.

- `/usr/share/lxc/config/nixos.common.conf`
- `/usr/share/lxc/config/nixos.userns.conf`
- `/usr/share/lxc/hooks/nixos-proxnix-prestart`
- `/usr/share/lxc/hooks/nixos-proxnix-mount`
- `/usr/share/lxc/hooks/nixos-proxnix-poststop`
- `/usr/local/lib/proxnix/pve-conf-to-nix.py`
- `/usr/local/lib/proxnix/nixos-proxnix-common.sh`
- `/usr/local/lib/proxnix/proxnix-secrets-guest`
- `/usr/local/sbin/proxnix-create-lxc`
- `/usr/local/sbin/proxnix-doctor`
- `proxnix-gc.service` and `proxnix-gc.timer`

### Node-local relay cache

These live on the local node under `/var/lib/proxnix/`. They are no longer the source of truth; your workstation publishes them there.

- `/var/lib/proxnix/base.nix`
- `/var/lib/proxnix/common.nix`
- `/var/lib/proxnix/security-policy.nix`
- `/var/lib/proxnix/configuration.nix`
- `/var/lib/proxnix/site.nix`
- `/var/lib/proxnix/containers/`
- `/etc/proxnix/host_relay_identity`
- `/var/lib/proxnix/private/containers/`

## Step 1: Install on the Proxmox host

Choose one of the supported installation paths. After installation, the node no
longer depends on the original proxnix repo checkout for normal use or
uninstall.

### Option A: Run the host helper script

This is the preferred host install path. The helper script resolves the latest
matching `proxnix-host` `.deb` for the node architecture, downloads it, and
installs it with `apt`.

Install the latest tagged release directly on the Proxmox node:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/install-host-package.sh)"
```

Install a specific version:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/install-host-package.sh)" -- --version 0.1.0
```

This keeps the user-facing install to one command while retaining package-owned
upgrades and removal underneath.

### Option B: Install the Debian package manually

If you want to build the `.deb` locally from the repo root:

```bash
./host/packaging/package-deb.sh
```

Then install it on the Proxmox node:

```bash
apt install ./dist/proxnix-host_<version>_<arch>.deb
```

Remove it later with:

```bash
apt remove proxnix-host
```

See [Host Packages](../operations/host-packages.md).

### Option C: Run the remote bootstrapper

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/github-install.sh)"
```

Use `--dry-run` to preview what would be installed without writing anything:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/github-install.sh)" -- --dry-run
```

### Option D: Run the shell installer from a local checkout

```bash
git clone <this repo>
cd proxnix
host/install.sh
```

You can delete that checkout afterwards if you want. The installed node keeps
its own `proxnix-uninstall` command.

### Option E: Deploy with Ansible over SSH

Run this from your workstation or another Ansible control machine, not from the
target node itself. The playbook copies the proxnix files from your local repo
checkout to each remote Proxmox host over SSH.

```bash
ansible-playbook -i host/inventory.proxmox.ini host/ansible/install.yml
```

The example inventory in `host/inventory.proxmox.ini` already sets
`ansible_connection=ssh` and `ansible_user=root`. `proxmox_cluster` is defined
as a child group of `proxmox`, so either target works without inventory
warnings. Make sure your control machine has SSH access to the listed hosts.

By default the playbook targets the `proxmox` inventory group. Override that
group when needed:

```bash
ansible-playbook -i host/inventory.proxmox.ini host/ansible/install.yml -e proxnix_target_hosts=proxmox_cluster
```

If you want one playbook for host install plus workstation config, use one of
the AI-oriented wrappers instead:

- `host/ansible/ai-agent-bootstrap.yml` for install + host verification + workstation config, without publishing a live site repo
- `host/ansible/ai-agent-deploy.yml` for the full publish flow

## Step 2: Create the workstation site repo

Keep live state outside this install repo. A typical layout looks like this:

```text
proxnix-site/
├── site.nix
├── containers/
│   └── <vmid>/
│       └── dropins/
└── private/
    ├── host_relay_identity.sops.yaml
    ├── shared/
    │   └── secrets.sops.yaml
    ├── groups/
    │   └── <group>/
    │       └── secrets.sops.yaml
    └── containers/
        └── <vmid>/
            ├── age_identity.sops.yaml
            └── secrets.sops.yaml
```

`site.nix`, `containers/`, encrypted secret stores, and encrypted private identities all live here and can be Git-tracked independently of the install repo.

## Step 3: Configure your workstation

Install the workstation CLI and TUI first:

```bash
pip install proxnix-workstation
```

Or with the repo helper:

```bash
./ci/install-workstation.sh
```

If you want to keep the tooling repo-local instead of changing the global
Python environment, install it into `workstation/.venv` and use the wrappers
under `workstation/bin/`:

```bash
./ci/bootstrap-workstation-venv.sh
```

Create `~/.config/proxnix/config`:

```bash
mkdir -p ~/.config/proxnix
cat > ~/.config/proxnix/config << 'EOF'
PROXNIX_SITE_DIR=~/src/proxnix-site
PROXNIX_HOSTS="root@node1 root@node2"
# Optional when your SSH agent/config already handles auth:
# PROXNIX_SSH_IDENTITY=~/.ssh/id_ed25519
# Only needed for the embedded-sops provider:
PROXNIX_SOPS_MASTER_IDENTITY=~/.ssh/proxnix-master
EOF
```

| Variable | Purpose | Default |
|----------|---------|---------|
| `PROXNIX_SITE_DIR` | Local proxnix site repo | *(required)* |
| `PROXNIX_HOSTS` | Space-separated SSH targets used by `proxnix-publish` | *(required for publishing)* |
| `PROXNIX_SSH_IDENTITY` | Optional SSH private key used to connect to relay hosts; if unset, use normal SSH agent/config | *(optional)* |
| `PROXNIX_SOPS_MASTER_IDENTITY` | SSH private key used by the `embedded-sops` provider for encrypted identities and stores | `~/.ssh/id_ed25519` |
| `PROXNIX_REMOTE_DIR` | Relay cache dir on the Proxmox host | `/var/lib/proxnix` |
| `PROXNIX_REMOTE_PRIV_DIR` | Relay cache private dir on the Proxmox host | `/var/lib/proxnix/private` |
| `PROXNIX_REMOTE_HOST_RELAY_IDENTITY` | Host path for the plaintext host relay key | `/etc/proxnix/host_relay_identity` |

If you only want a fresh host/bootstrap first, you can stop after rendering
this config and verifying `proxnix-doctor --host-only` on the target nodes.
You do not need to publish the live site repo in the same run.

### Required workstation tools

- `ssh`
- `rsync`
- `sops`
- `python3`

`pip install proxnix-workstation` installs both the unified `proxnix` CLI and
the terminal UI entrypoint:

```bash
proxnix
proxnix-tui
```

On Apple Silicon macOS, the Homebrew tap can also ship the same terminal tools as:

```bash
brew install denMaier/tap/proxnix-workstation
```

### Proxnix Manager on macOS

`Proxnix Manager` is the Electrobun workstation GUI. It is intended to ship from
a Homebrew tap so the macOS app can be installed with a single
`brew install --cask` command. This repo includes the tap cask scaffold under
`packaging/homebrew/`.

See [Proxnix Manager](../operations/proxnix-manager.md).

If you prefer Nix-managed installs on `nixos` or `nix-darwin`, this repo now
exports `./workstation#proxnix-workstation` and
`./workstation#proxnix-workstation-cli` via `workstation/flake.nix`. See
[Workstation Packages](../operations/workstation-packages.md).

### Required Proxmox host runtime tool

- `sops` — the pre-start hook uses the shared host relay key to decrypt guest identities just before staging them into the CT

## Step 4: Initialize identities and secrets

Initialize the shared host relay identity once for the site:

```bash
proxnix-secrets init-host-relay
```

Initialize a per-container identity explicitly when you want one before writing secrets:

```bash
proxnix-secrets init-container 120
```

You usually do not need to run `init-container` manually because `proxnix-secrets set <vmid> ...` creates the identity on demand.

To set the default admin password hash:

```bash
mkpasswd -m sha-512
proxnix-secrets set-shared common_admin_password_hash
```

## Step 5: Publish relay state to the nodes

Publish the workstation-owned site repo to every relay host:

```bash
proxnix-publish
```

Or only to a subset:

```bash
proxnix-publish root@node1
```

This pushes:

- `site.nix`
- `containers/<vmid>/...`
- per-container compiled secret stores
- the shared plaintext host relay key under `/etc/proxnix/host_relay_identity`
- container identities re-encrypted at rest for both the host relay key and the master recovery key under `/var/lib/proxnix/private/...`

That means each Proxmox host persistently stores only one plaintext relay key. Guest identities remain encrypted at rest on the host and are decrypted only transiently during the pre-start staging flow. In practice the Proxmox host is still the trust boundary for secret relay.

## Upgrading proxnix files

If the install repo changes `base.nix`, `common.nix`, `security-policy.nix`, or `configuration.nix`,
reinstall proxnix on each node.

If you are using the Debian package path, install the updated package:

```bash
apt install ./dist/proxnix-host_<version>_<arch>.deb
```

If you are still using the shell installer path:

```bash
host/install.sh
```

Or redeploy them remotely from your Ansible control machine:

```bash
ansible-playbook -i host/inventory.proxmox.ini host/ansible/install.yml
```

After upgrading, restart managed containers so they pick up the new hook/runtime code.

## Uninstalling

To remove proxnix from a node but keep the published relay cache:

```bash
apt remove proxnix-host
```

If that node was installed with the shell installer rather than the package,
use:

```bash
proxnix-uninstall
```

Both paths remove only the installed hooks, helpers, and timers. They
intentionally leave `/var/lib/proxnix` and `/etc/proxnix` alone.

## What you should have when done

On the workstation:

```text
~/.config/proxnix/config
~/src/proxnix-site/
├── site.nix
├── containers/
└── private/
```

On each Proxmox node:

```text
/var/lib/proxnix/
├── base.nix
├── common.nix
├── security-policy.nix
├── configuration.nix
├── site.nix
├── containers/
└── private/
    └── containers/

/etc/proxnix/
└── host_relay_identity
```

Proceed to [first container](first-container.md) to onboard your first NixOS LXC.
