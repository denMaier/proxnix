# Installation

This page covers installing proxnix on Proxmox nodes and setting up the workstation-owned site repo that proxnix now uses as its source of truth.

## Checklist

- [ ] Install proxnix on every Proxmox node with `ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml` from your control machine
- [ ] Create a separate workstation-owned site repo, or stop after workstation config if you only want a fresh host/bootstrap first
- [ ] Configure your workstation for `proxnix-secrets` and `proxnix-publish`
- [ ] Initialize the host relay identity
- [ ] Publish the site repo to every Proxmox node that should relay it

## What the node install does

Every Proxmox node that may start proxnix-managed containers needs the same
installed assets. The production host deployment path is
`host/deploy/ansible/install.yml` from an Ansible control machine over SSH.

It installs two kinds of assets:

### Per-node files

These are installed locally on each node because reconciliation and helper
commands execute on the node that owns the CT.

- `/usr/share/lxc/config/nixos.common.conf`
- `/usr/share/lxc/config/nixos.userns.conf`
- `/usr/share/lxc/hooks/nixos-proxnix-start-host`
- `/usr/local/lib/proxnix/proxnix-secrets-guest`
- `/usr/local/sbin/proxnix-host`
- `/usr/local/sbin/proxnix-doctor`
- `/usr/local/sbin/proxnix-host-activate`
- `/usr/local/sbin/proxnix-host-uninstall`
- `proxnix-gc.service` and `proxnix-gc.timer`
- `proxnix-flake-update.service` and `proxnix-flake-update.timer`
- `proxnix-reconcile.service`, `proxnix-reconcile.timer`, and `proxnix-reconcile@.service`

### Node-local relay cache

These live on the local node under `/var/lib/proxnix/`. They are no longer the source of truth; your workstation publishes them there.

- `/var/lib/proxnix/base.nix`
- `/var/lib/proxnix/common.nix`
- `/var/lib/proxnix/security-policy.nix`
- `/var/lib/proxnix/configuration.nix`
- `/var/lib/proxnix/authority/`
- `/var/lib/proxnix/status/`
- `/var/lib/proxnix/site.nix`
- `/var/lib/proxnix/containers/`
- `/etc/proxnix/host_relay_identity`
- `/var/lib/proxnix/private/containers/`

## Step 1: Install on the Proxmox host

Host-side reconciliation makes Nix a required Proxmox-node runtime dependency.
The playbook checks whether Nix is installed, enables `nix-command flakes`,
installs or upgrades the `/nix/var/nix/profiles/proxnix-host` profile, runs `proxnix-host-activate`, and
verifies the installed commands. Activation links the proxnix files from the Nix
profile into the mutable Proxmox paths that LXC and systemd expect. Run the
playbook from your workstation or another Ansible control machine, not from the
target node itself. By default it installs `github:denMaier/proxnix#proxnix-host`;
pin a release or branch by overriding `proxnix_host_flake_ref`.

The default install path assumes Nix is already installed. If you want the
playbook to bootstrap Nix as a convenience, pass
`-e proxnix_nix_install_mode=determinate`; this uses the Determinate Systems
installer when Nix is absent.

The installer builds the requested host package first, switches the Nix profile,
then activates the profile idempotently. Activation overwrites proxnix-managed
links in place and preserves deployment GC roots under
`/var/lib/proxnix/gcroots/deploy`; prune them with `proxnix-host gc` instead of
the installer. It does not wipe relay data or host secrets under
`/var/lib/proxnix/authority`, `/var/lib/proxnix/containers`,
`/var/lib/proxnix/private`, or `/etc/proxnix`. Use
`-e proxnix_install_clean_slate=true` only as an explicit repair/reset path for
stale pre-Nix or broken installs.

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml
```

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml \
  -e proxnix_host_flake_ref='github:denMaier/proxnix?ref=v0.6.1#proxnix-host'
```

For development against the current checkout, use `install-local.yml`. It stages
a small tar archive of the host-side flake inputs under
`/var/lib/proxnix/install-source`, then calls the normal installer with that
local flake ref. The development path resets that source directory before
staging while keeping the freshly staged source available for inspection after
activation.

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install-local.yml
```

If the install should also coordinate the proxnix golden LXC template storage,
pass the template vars explicitly. `proxnix_template_storage` is the Proxmox
storage ID, not a filesystem path. On shared storage the installer acquires a
storage-wide lock before it starts the host install work, so simultaneous
installs on all nodes serialize the storage bootstrap path and only one node
controls template download/creation at a time.

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml \
  -e proxnix_target_hosts=proxmox_cluster \
  -e proxnix_template_bootstrap_enabled=true \
  -e proxnix_template_bootstrap_dry_run=true \
  -e proxnix_template_storage=mooseFS \
  -e proxnix_template_source_name=nixos-golden-25.11.tar.xz \
  -e proxnix_template_rootfs_storage=local-zfs \
  -e proxnix_template_name=proxnix-nixos-golden.tar.xz
```

Use `-e proxnix_template_force=true` when you intentionally want to refresh an
existing shared template. The force path still runs under the same storage lock.
The bootstrap creates a temporary stopped CT from the downloaded NixOS LXC
template, mounts it, imports its system closure into the host store, destroys
the temporary CT, and builds the host-pinned proxnix golden closure.

`proxnix-host-uninstall` removes the proxnix host symlinks and Nix profile, but
does not remove Nix itself. For a Determinate-installed Nix, remove Nix
separately with:

```bash
/nix/nix-installer uninstall
```

The example inventory in `host/deploy/inventory.proxmox.ini` already sets
`ansible_connection=ssh` and `ansible_user=root`. `proxmox_cluster` is defined
as a child group of `proxmox`, so either target works without inventory
warnings. Make sure your control machine has SSH access to the listed hosts.

By default the playbook targets the `proxmox` inventory group. Override that
group when needed:

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml -e proxnix_target_hosts=proxmox_cluster
```

If you want one playbook for host install plus workstation config, use one of
the AI-oriented wrappers instead:

- `host/deploy/ansible/ai-agent-bootstrap.yml` for install + host verification + workstation config, without publishing a live site repo
- `host/deploy/ansible/ai-agent-deploy.yml` for the full publish flow

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
under `workstation/cli/bin/`:

```bash
./ci/bootstrap-workstation-venv.sh
```

For local Proxnix Manager development with the `pykeepass` provider, install
`pykeepass` into that virtualenv:

```bash
workstation/.venv/bin/python -m pip install pykeepass
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

Packaged Manager builds include the workstation CLI wrappers and core Python
dependencies inside the app bundle. Development builds prefer
`workstation/.venv/bin/python`, so optional Python providers such as
`pykeepass` should be installed into that venv rather than the system Python.
For custom Manager-only Python modules, set `PROXNIX_MANAGER_PYTHONPATH` in the
Manager settings or config file.

See [Proxnix Manager](../operations/proxnix-manager.md).

If you prefer Nix-managed installs on `nixos` or `nix-darwin`, this repo now
exports `./workstation#proxnix-workstation` and
`./workstation#proxnix-workstation-cli` via `workstation/flake.nix`. See
[Workstation Packages](../operations/workstation-packages.md).

### Required Proxmox host runtime tool

- `sops` — the reconciler uses the shared host relay key to decrypt guest identities just before staging them into the CT

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

That means each Proxmox host persistently stores only one plaintext relay key. Guest identities remain encrypted at rest on the host and are decrypted only transiently during reconcile staging. In practice the Proxmox host is still the trust boundary for secret relay.

## Upgrading proxnix files

If the host runtime, systemd units, or shared Nix modules change, rerun
the Ansible playbook from your control machine:

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml
```

After upgrading, reconcile managed containers so they pick up the new runtime code.

## Uninstalling

To remove proxnix from a node but keep the published relay cache, use the
uninstall helper installed by the Ansible playbook:

```bash
proxnix-host-uninstall
```

This removes only the installed helpers, services, timers, and proxnix host profile. It
intentionally leaves `/var/lib/proxnix` and `/etc/proxnix` state alone.

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
