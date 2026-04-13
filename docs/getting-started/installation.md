# Installation

This page covers installing proxnix on Proxmox nodes and setting up the workstation-owned site repo that proxnix now uses as its source of truth.

## Checklist

- [ ] Run `./install.sh` or `ansible-playbook ansible/install.yml` on every Proxmox node
- [ ] Create a separate workstation-owned site repo
- [ ] Configure your workstation for `proxnix-secrets` and `proxnix-publish`
- [ ] Initialize the shared identity if you plan to use shared secrets
- [ ] Publish the site repo to every Proxmox node that should relay it

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
- `/usr/local/sbin/proxnix-create-lxc`
- `/usr/local/sbin/proxnix-doctor`
- `proxnix-gc.service` and `proxnix-gc.timer`

### Node-local relay cache

These live on the local node under `/var/lib/proxnix/`. They are no longer the source of truth; your workstation publishes them there.

- `/var/lib/proxnix/base.nix`
- `/var/lib/proxnix/common.nix`
- `/var/lib/proxnix/configuration.nix`
- `/var/lib/proxnix/site.nix`
- `/var/lib/proxnix/containers/`
- `/var/lib/proxnix/private/shared_age_identity.txt`
- `/var/lib/proxnix/private/shared/`
- `/var/lib/proxnix/private/containers/`

## Step 1: Install on the Proxmox host

Choose one of the supported installation paths.

### Option A: Run the shell installer directly

```bash
git clone <this repo>
cd proxnix
./install.sh
```

Use `--dry-run` to preview what would be installed without writing anything.

### Option B: Install with Ansible

```bash
ansible-playbook -i inventory.ini ansible/install.yml
```

By default the playbook targets the `proxmox` inventory group. Override that group when needed:

```bash
ansible-playbook -i inventory.ini ansible/install.yml -e proxnix_target_hosts=pve_nodes
```

## Step 2: Create the workstation site repo

Keep live state outside this install repo. A typical layout looks like this:

```text
proxnix-site/
├── site.nix
├── containers/
│   └── <vmid>/
│       ├── proxmox.yaml
│       ├── user.yaml
│       ├── dropins/
│       └── quadlets/
└── private/
    ├── shared_age_identity.sops.json
    ├── shared/
    │   └── secrets.sops.yaml
    └── containers/
        └── <vmid>/
            ├── age_identity.sops.json
            └── secrets.sops.yaml
```

`site.nix`, `containers/`, encrypted secret stores, and encrypted private identities all live here and can be Git-tracked independently of the install repo.

## Step 3: Configure your workstation

Create `~/.config/proxnix/config`:

```bash
mkdir -p ~/.config/proxnix
cat > ~/.config/proxnix/config << 'EOF'
PROXNIX_SITE_DIR=~/src/proxnix-site
PROXNIX_MASTER_IDENTITY=~/.ssh/proxnix-master
PROXNIX_HOSTS="root@node1 root@node2"
PROXNIX_SSH_IDENTITY=~/.ssh/id_ed25519
EOF
```

| Variable | Purpose | Default |
|----------|---------|---------|
| `PROXNIX_SITE_DIR` | Local proxnix site repo | *(required)* |
| `PROXNIX_MASTER_IDENTITY` | Local SSH private key used by SOPS for encrypted identities and stores | `~/.ssh/id_ed25519` |
| `PROXNIX_HOSTS` | Space-separated SSH targets used by `proxnix-publish` | *(required for publishing)* |
| `PROXNIX_SSH_IDENTITY` | SSH private key used to connect to relay hosts | `~/.ssh/id_ed25519` |
| `PROXNIX_REMOTE_DIR` | Relay cache dir on the Proxmox host | `/var/lib/proxnix` |
| `PROXNIX_REMOTE_PRIV_DIR` | Relay cache private dir on the Proxmox host | `/var/lib/proxnix/private` |

### Required workstation tools

- `ssh`
- `ssh-keygen`
- `rsync`
- `sops`
- `python3`

## Step 4: Initialize identities and secrets

Initialize the shared identity if you plan to use shared secrets:

```bash
proxnix-secrets init-shared
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
- encrypted secret stores
- decrypted relay identities under `/var/lib/proxnix/private/...`

The host cache contains plaintext relay identities because proxnix must be able to restage them into guests on every boot without workstation access. In practice that means the Proxmox host is the trust boundary for secret relay.

## Upgrading proxnix files

If the install repo changes `base.nix`, `common.nix`, or `configuration.nix`, reinstall proxnix on each node:

```bash
./install.sh
```

Or, if you installed with Ansible:

```bash
ansible-playbook -i inventory.ini ansible/install.yml
```

After upgrading, restart managed containers so they pick up the new hook/runtime code.

## Uninstalling

To remove proxnix from a node but keep the published relay cache:

```bash
./uninstall.sh
```

This removes only the installed hooks, helpers, and timers. It intentionally leaves `/var/lib/proxnix` alone.

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
├── configuration.nix
├── site.nix
├── containers/
└── private/
    ├── shared_age_identity.txt
    ├── shared/
    └── containers/
```

Proceed to [first container](first-container.md) to onboard your first NixOS LXC.
