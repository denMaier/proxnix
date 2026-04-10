# proxnix

NixOS LXC containers on Proxmox, managed from the host.

No Flakes. Static IPs. Key-only SSH. Secrets via age.

---

## How it works

A pre-start hook runs on the Proxmox host every time a managed container starts. It writes the host-owned NixOS config into `/etc/nixos/managed/`, keeps the managed files read-only, and updates a config hash when that tree changes. A boot-time `proxnix-apply-config` unit then runs `nixos-rebuild switch` only when the desired hash differs from the last applied hash.

Network config (hostname, IP, gateway, DNS, SSH keys) is read from the Proxmox container config and turned into a `proxmox.nix` by `yaml-to-nix.py`. You set these in the WebUI; proxnix mirrors them into Nix.

---

## Install (Proxmox host, run as root)

```bash
git clone <this repo>
cd proxnix
./install.sh
```

On the first cluster node this creates `/etc/pve/proxnix/` and `/etc/pve/priv/proxnix/`. `pmxcfs` replicates those to all other nodes automatically. Run `./install.sh` on every other node too — it skips the shared tree and only installs the per-node files.

After install, store your master public key (used for secret recovery):

```bash
# SSH public key works:
ssh-keygen -y -f ~/.ssh/id_ed25519 > /etc/pve/proxnix/master_age_pubkey
# or a dedicated age key:
age-keygen 2>&1 | grep 'public key' | awk '{print $NF}' > /etc/pve/proxnix/master_age_pubkey
```

---

## New container

**1. Create the container:**

Download the template via the WebUI (Storage → CT Templates → Download from URL) using a tarball from [Hydra](https://hydra.nixos.org/project/nixos) → Jobs → `nixos.proxmoxLXC`. Then create it normally through the wizard.

There is no OS type dropdown — Proxmox auto-detects `ostype=nixos` from `/etc/os-release` inside the tarball. Verify after creation with `pct config <vmid> | grep ostype`. If it shows something else (e.g. you used a generic NixOS tarball), fix it with `pct set <vmid> --ostype nixos`.

`ostype=nixos` is what makes Proxmox auto-include `nixos.common.conf` and activate the proxnix hook. Set SSH public keys in the wizard — proxnix mirrors them into Nix. Skip the root password field — proxnix overwrites it with a locked password on the first rebuild; SSH key access is the only way in after that.

**2. Optional: add per-container config on the host:**

```bash
VMID=100
mkdir -p /etc/pve/proxnix/containers/$VMID/dropins

# extras the WebUI can't express (search domain, additional ssh keys):
cp proxmox.yaml /etc/pve/proxnix/containers/$VMID/proxmox.yaml

# native NixOS services (Jellyfin, Immich, ...):
cp user.yaml /etc/pve/proxnix/containers/$VMID/user.yaml

# Podman workloads — drop raw Quadlet files here:
$EDITOR /etc/pve/proxnix/containers/$VMID/dropins/myapp.container
```

**3. Start the container:**

```bash
pct start 100
# watch config activation:
pct exec 100 -- journalctl -fu proxnix-apply-config
```

**4. Bootstrap secrets:**

```bash
./bootstrap.sh 100   # extracts the container's age public key
```

Follow the printed encryption command to add your first secrets, then `pct restart 100` to push them.

---

## Day-to-day

**Change network config / SSH keys:** edit the container in the WebUI (or `proxmox.yaml` for extras), restart the container.

**Add/change a container workload:** edit the Quadlet file in `dropins/` on the host, restart the container.

**Push a NixOS config change:** edit `proxmox.yaml`, `user.yaml`, or host-managed `.nix` files, then restart the container. For ad-hoc guest-only overrides, place them in `/etc/nixos/local.nix` and run `nixos-rebuild switch` manually.

**Health check:**
```bash
proxnix-doctor 100
proxnix-doctor --all
```

---

## Secrets

Secrets are age-encrypted files stored on the Proxmox host under `/etc/pve/priv/proxnix/`. The pre-start hook copies them into `/etc/secrets/` inside the container on every start and registers them with Podman's shell driver so `podman run --secret name` works without any manual setup.

Every `.age` file is encrypted to two recipients: the container's own age public key and your master key. The container's private key never leaves the container; your master key stays off the cluster and is used for recovery or rotation.

**Manage secrets from your workstation** (install `proxnix-secrets` locally):

```bash
cp proxnix-secrets ~/.local/bin/
chmod +x ~/.local/bin/proxnix-secrets
```

Config (`~/.config/proxnix/config`):
```bash
PROXNIX_HOST=root@proxmox
PROXNIX_IDENTITY=~/.age/identity.txt   # or ~/.ssh/id_ed25519
```

Common commands:
```bash
proxnix-secrets ls 100                 # list effective secrets for a container
proxnix-secrets set 100 db_password    # prompt for value, encrypt, push
proxnix-secrets get 100 db_password    # decrypt and print
proxnix-secrets rm  100 db_password
proxnix-secrets rotate 100 db_password # re-encrypt (after key rotation)

proxnix-secrets set-shared db_password # push to every bootstrapped container
proxnix-secrets rotate-shared --all    # re-encrypt all shared secrets
```

After adding or removing secrets, `pct restart <vmid>` to push the changes.

**Use a secret in a Quadlet container:**
```ini
[Container]
Secret=db_password,type=env,target=DB_PASSWORD
```

**Use a secret in a native NixOS service** — declare in `user.yaml`:
```yaml
services:
  immich:
    secrets:
      - name: db_password
        path: /run/immich-secrets/db_password
```
Then point the service at the path in a dropin `.nix`:
```nix
{ ... }: { services.immich.database.passwordFile = "/run/immich-secrets/db_password"; }
```

The decryption happens in an `ExecStartPre` step; the plaintext lives in a tmpfs directory for the service lifetime only.

---

## File layout

### On each Proxmox node (local, not replicated)

| Path | Purpose |
|------|---------|
| `/usr/share/lxc/config/nixos.common.conf` | Auto-included for `ostype=nixos`; registers the hook |
| `/usr/share/lxc/config/nixos.userns.conf` | Unprivileged container overrides |
| `/usr/share/lxc/hooks/nixos-proxnix-prestart` | The hook — runs on every `pct start` |
| `/usr/local/lib/proxnix/yaml-to-nix.py` | PVE conf + YAML → Nix converter |
| `/usr/local/sbin/proxnix-doctor` | Health checker |

### Cluster-wide via pmxcfs (`/etc/pve/`)

| Path | Purpose |
|------|---------|
| `proxnix/master_age_pubkey` | Your public key for secret recovery |
| `proxnix/base.nix` | Shared baseline pushed into every container |
| `proxnix/common.nix` | Admin user, SSH hardening, journald, timesyncd |
| `proxnix/configuration.nix` | NixOS entrypoint (imports base + dropins) |
| `proxnix/chezmoi.nix` | App-config management module |
| `proxnix/containers/<vmid>/proxmox.yaml` | Optional network extras and SSH keys |
| `proxnix/containers/<vmid>/user.yaml` | Native NixOS service config |
| `proxnix/containers/<vmid>/age_pubkey` | Container's age public key (written by `bootstrap.sh`) |
| `proxnix/containers/<vmid>/dropins/` | `.nix` fragments and Quadlet files |
| `priv/proxnix/shared/*.age` | Shared encrypted secrets (all bootstrapped containers) |
| `priv/proxnix/containers/<vmid>/secrets/*.age` | Per-container encrypted secrets |

### Inside each managed container (`/etc/nixos/`)

`configuration.nix` is host-managed and imports the read-only tree under `/etc/nixos/managed/`. `local.nix` is the optional guest-local escape hatch.

Inside `/etc/nixos/managed/` the hook writes `base.nix`, `common.nix`, `chezmoi.nix`, `proxmox.nix` (generated from PVE conf + `proxmox.yaml`), `user.nix` (generated from `user.yaml`), and `dropins/*.nix`.

---

## Access policy (`common.nix`)

- Creates an `admin` user (UID 1000, `wheel`) with the same SSH keys as `root`
- Reads the hashed password from the shared secret `common_admin_password_hash` — until that secret exists the account is SSH-key-only
- `sudo` requires the password (`wheelNeedsPassword = true`)
- Root password is locked; `PermitRootLogin = prohibit-password`

To set the shared admin password hash:
```bash
# generate: openssl passwd -6 or mkpasswd -m sha-512
printf '$6$...' | proxnix-secrets set-shared common_admin_password_hash
proxnix-secrets rotate-shared common_admin_password_hash
```
