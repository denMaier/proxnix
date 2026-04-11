# proxnix

NixOS LXC containers on Proxmox, managed from the host.

No Flakes. Static IPs. Key-only SSH. Secrets via SOPS + age.

---

## How it works

Every time a managed container starts, two hooks run on the Proxmox host:

1. **Pre-start** — renders the desired guest state (Nix configs, secrets, Quadlet files) into a staging directory under `/run/proxnix/<vmid>/`
2. **Mount** — copies that staged state into the container's rootfs before the first process starts

Network config (hostname, IP, gateway, DNS, SSH keys) is read from the Proxmox container config and turned into a `proxmox.nix` by `yaml-to-nix.py`. You set these in the WebUI; proxnix mirrors them into Nix.

Inside the container, the mount hook installs a host-managed `proxnix-apply-config` service under `/etc/systemd/system.attached/`. It runs `nixos-rebuild switch` once per boot when the managed config hash changes. Podman is enabled only when top-level Quadlet unit files are present.

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
ssh-keygen -y -f ~/.ssh/id_ed25519 > /etc/pve/proxnix/master_age_pubkey
```

If you plan to use shared secrets, initialize the shared keypair (once per cluster):

```bash
proxnix-secrets init-shared
```

---

## New container

**1. Create the container**

Download the template via the WebUI (Storage → CT Templates → Download from URL) using a tarball from [Hydra](https://hydra.nixos.org/project/nixos) → Jobs → `nixos.proxmoxLXC`. Then create it normally through the wizard.

There is no OS type dropdown — Proxmox auto-detects `ostype=nixos` from `/etc/os-release` inside the tarball. Verify after creation:

```bash
pct config <vmid> | grep ostype
```

If it shows something other than `nixos` (e.g. you used a generic NixOS tarball), fix it:

```bash
pct set <vmid> --ostype nixos
```

`ostype=nixos` is what makes Proxmox auto-include `nixos.common.conf` and activate the proxnix hooks. Set SSH public keys in the wizard — proxnix mirrors them into Nix. Skip the root password field — proxnix locks it on the first rebuild; SSH key access is the only way in after that.

**2. Add per-container config (optional)**

```bash
VMID=100
mkdir -p /etc/pve/proxnix/containers/$VMID/quadlets

# extras the WebUI can't express (search domain, additional SSH keys):
cp proxmox.yaml /etc/pve/proxnix/containers/$VMID/proxmox.yaml

# native NixOS services (Jellyfin, Immich, ...):
cp user.yaml /etc/pve/proxnix/containers/$VMID/user.yaml

# Podman workloads — drop raw Quadlet files here.
# Unit files go to /etc/containers/systemd; non-unit config goes to
# /etc/proxnix/quadlets and is tracked with jj.
$EDITOR /etc/pve/proxnix/containers/$VMID/quadlets/myapp.container
```

**3. Start the container**

```bash
pct start 100
```

**4. First-boot bootstrap**

The Hydra template ships without a nixpkgs channel, so `proxnix-apply-config` cannot run on the very first boot. Bootstrap manually:

> **RAM:** NixOS evaluation needs at least **2 GB RAM**. Set this in the container's Resources tab before starting.

```bash
pct enter 100
~/proxnix-bootstrap.sh
```

The script adds the correct nixos channel, updates it, and runs `nixos-rebuild switch`. After it completes the container is fully configured and subsequent config changes are applied automatically on the next container boot.

If you get a permission error on the script, run it explicitly:
```bash
bash ~/proxnix-bootstrap.sh
```

**5. Bootstrap secrets**

```bash
./bootstrap.sh 100
```

This reads the container's age public key and stores it on the host so you can encrypt secrets for it. Follow the printed instructions to add your first secret, then restart the container to push it.

---

## Day-to-day

**Change network config / SSH keys:** edit the container in the WebUI (or `proxmox.yaml` for extras), restart the container.

**Add/change a Podman workload:** edit files under `quadlets/` on the host, restart the container. Inside the guest, proxnix puts Quadlet unit files directly into `/etc/containers/systemd`, while non-unit app config is mirrored into `/etc/proxnix/quadlets` and tracked with `jj`.

**Push a NixOS config change:** edit `user.yaml` or a dropin `.nix` file, then restart the container. The next boot schedules one guarded `nixos-rebuild switch` automatically. For ad-hoc guest-only changes, put them in `/etc/nixos/local.nix` inside the container and run `nixos-rebuild switch` manually.

**Secrets:** manage scalar secrets with `proxnix-secrets`. Native services and Quadlet containers both read from the same staged SOPS-backed store inside the guest.

**Health check:**
```bash
proxnix-doctor 100
proxnix-doctor --all
```

---

## Secrets

Secrets are SOPS-encrypted YAML stores under `/etc/pve/priv/proxnix/`. There is one shared store and one optional store per container. On start, proxnix stages the shared and container stores into `/etc/proxnix/secrets/` inside the guest, merges their visible keys for Podman registration, and uses the same `proxnix-secrets` helper for native service extraction.

### Setup

Install `proxnix-secrets` on your **local workstation**:

```bash
cp proxnix-secrets ~/.local/bin/
chmod +x ~/.local/bin/proxnix-secrets
```

The guest-side helper is separate: `install.sh` installs `proxnix-secrets-guest` on the Proxmox node, and the mount hook injects it into each managed container as `/usr/local/bin/proxnix-secrets`.

Create `~/.config/proxnix/config`:
```bash
PROXNIX_HOST=root@proxmox
PROXNIX_IDENTITY=~/.config/age/identity.txt   # or ~/.ssh/id_ed25519
```

### Per-container secrets

Encrypted for the container's own age key and your master key. Only that container can decrypt its per-container store.

```bash
proxnix-secrets set 100 db_password    # prompt for value, encrypt, push
proxnix-secrets get 100 db_password    # decrypt and print
proxnix-secrets rm  100 db_password
proxnix-secrets rotate 100             # re-encrypt the container store after key rotation
proxnix-secrets ls 100                 # list secrets for a container
```

### Shared secrets

Encrypted for a single shared age keypair and your master key. Every container receives the shared private key automatically, so any container can decrypt shared secrets. Container keys override shared keys with the same name.

**One-time setup** (already done if you ran `init-shared` after install):
```bash
proxnix-secrets init-shared
```

**Manage shared secrets:**
```bash
proxnix-secrets set-shared db_password   # encrypt for shared keypair
proxnix-secrets get-shared db_password   # decrypt and print
proxnix-secrets rm-shared  db_password
proxnix-secrets ls-shared
```

**Rotate the shared keypair** (use after a key leak):
```bash
proxnix-secrets rotate-shared
```
This re-encrypts the shared YAML store for the current shared and master recipients. Restart each container to pick up staged changes.

### Using secrets

After adding or removing secrets, restart the container to push the changes:
```bash
pct restart 100
```

**In a Quadlet container:**
```ini
[Container]
Secret=db_password,type=env,target=DB_PASSWORD
```

**In a native NixOS service** — declare in `user.yaml`:
```yaml
services:
  immich:
    secrets:
      - name: db_password
        path: /run/immich-secrets/db_password
```
Then reference the path in a dropin `.nix`:
```nix
{ ... }: { services.immich.database.passwordFile = "/run/immich-secrets/db_password"; }
```
The extraction happens in an `ExecStartPre` step through the guest-side `proxnix-secrets` helper; the plaintext lives in a tmpfs directory for the service lifetime only.

---

## Access policy (`common.nix`)

- Creates an `admin` user (UID 1000, `wheel`) with the same SSH keys as `root`
- Reads the hashed password from the shared secret `common_admin_password_hash` — until that secret exists the account is SSH-key-only
- `sudo` requires the password (`wheelNeedsPassword = true`)
- Root password is locked; `PermitRootLogin = prohibit-password`

To set the shared admin password hash:
```bash
# generate the hash first:
openssl passwd -6        # or: mkpasswd -m sha-512

printf '$6$...' | proxnix-secrets set-shared common_admin_password_hash
```

---

## File layout

### On each Proxmox node (local, not replicated)

| Path | Purpose |
|------|---------|
| `/usr/share/lxc/config/nixos.common.conf` | Auto-included for `ostype=nixos`; registers the hooks |
| `/usr/share/lxc/config/nixos.userns.conf` | Unprivileged container overrides |
| `/usr/share/lxc/hooks/nixos-proxnix-prestart` | Render hook — runs on every `pct start` |
| `/usr/share/lxc/hooks/nixos-proxnix-mount` | Rootfs sync hook — runs during the LXC mount phase |
| `/usr/local/lib/proxnix/yaml-to-nix.py` | PVE conf + YAML → Nix converter |
| `/usr/local/lib/proxnix/nixos-proxnix-common.sh` | Shared helper sourced by both hooks |
| `/usr/local/sbin/proxnix-doctor` | Health checker |

### Cluster-wide via pmxcfs (`/etc/pve/`)

| Path | Purpose |
|------|---------|
| `proxnix/master_age_pubkey` | Your public key for secret recovery |
| `proxnix/shared_age_pubkey` | Public key of the shared secret keypair |
| `proxnix/base.nix` | Shared baseline pushed into every container |
| `proxnix/common.nix` | Admin user, SSH hardening, journald, timesyncd |
| `proxnix/configuration.nix` | NixOS entrypoint (imports base + dropins) |
| `proxnix/containers/<vmid>/proxmox.yaml` | Optional network extras and SSH keys |
| `proxnix/containers/<vmid>/user.yaml` | Native NixOS service config |
| `proxnix/containers/<vmid>/age_pubkey` | Container's age public key (written by `bootstrap.sh`) |
| `proxnix/containers/<vmid>/dropins/` | Optional `.nix` fragments and legacy flat Quadlet files |
| `proxnix/containers/<vmid>/quadlets/` | Quadlet unit files and app config synced into the guest |
| `priv/proxnix/shared_age_identity.txt` | Private key of the shared secret keypair |
| `priv/proxnix/shared/secrets.sops.yaml` | Shared encrypted secret store |
| `priv/proxnix/containers/<vmid>/secrets.sops.yaml` | Per-container encrypted secret store |

### Inside each managed container

`/etc/nixos/configuration.nix` is host-managed and imports the read-only tree under `/etc/nixos/managed/` (`base.nix`, `common.nix`, `proxmox.nix`, `user.nix`, `dropins/*.nix`). `/etc/nixos/local.nix` is the optional guest-local escape hatch.

Non-unit Quadlet config is mirrored into `/etc/proxnix/quadlets`, which proxnix initializes as a `jj` repository. Quadlet unit files are copied directly into `/etc/containers/systemd/`.

| Path | Purpose |
|------|---------|
| `/etc/age/identity.txt` | Container's own age private key (generated on first boot) |
| `/etc/age/shared_identity.txt` | Shared age private key (present when `init-shared` has been run) |
| `/etc/proxnix/secrets/{shared,container}.sops.yaml` | Staged SOPS secret stores |
| `/etc/proxnix/quadlets/` | `jj`-tracked app config for Quadlet workloads |
| `/etc/containers/systemd/` | Quadlet unit files consumed by Podman/systemd |
| `/etc/secrets/.ids/` | Podman shell-driver UUID to name mappings |
