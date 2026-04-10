# proxNix — NixOS LXC Template System for Proxmox

Opinionated, reproducible NixOS containers on Proxmox VE.  
No Flakes, stdlib-only tooling, static IPs, key-only SSH.

---

## File Map

### Inside every managed NixOS LXC (`/etc/nixos/`)

| File | Who writes it | Purpose |
|------|---------------|---------|
| `base.nix` | proxnix pre-start hook | Shared baseline for all managed containers |
| `configuration.nix` | proxnix pre-start hook | Entrypoint that imports the shared modules and drop-ins |
| `chezmoi.nix` | proxnix pre-start hook | Optional app-config management module |
| `proxmox.nix` | proxnix pre-start hook | Generated networking config from PVE config + optional `proxmox.yaml` |
| `user.nix` | proxnix pre-start hook | Generated services config from `user.yaml` |

### On each Proxmox node (local filesystem)

| Path | Purpose |
|------|---------|
| `/usr/share/lxc/config/nixos.common.conf` | Auto-enables the proxnix pre-start hook for `ostype=nixos` containers |
| `/usr/share/lxc/config/nixos.userns.conf` | User namespace overrides for unprivileged NixOS LXCs |
| `/usr/share/lxc/hooks/nixos-proxnix-prestart` | Deploys config, secrets, and drop-ins into the container rootfs on every start |
| `/usr/local/lib/proxnix/yaml-to-nix.py` | Local YAML→Nix converter runtime |

### Shared across the cluster (`pmxcfs`)

| Path | Purpose |
|------|---------|
| `/etc/pve/proxnix/master_age_pubkey` | Your age or SSH public key for recovery / multi-recipient encryption |
| `/etc/pve/proxnix/base.nix` | Shared NixOS base config replicated via `pmxcfs` |
| `/etc/pve/proxnix/configuration.nix` | Shared NixOS entrypoint replicated via `pmxcfs` |
| `/etc/pve/proxnix/chezmoi.nix` | Shared chezmoi module replicated via `pmxcfs` |
| `/etc/pve/proxnix/containers/<vmid>/proxmox.yaml` | Optional per-container additions (for example `search_domain` or `ssh_keys`) |
| `/etc/pve/proxnix/containers/<vmid>/user.yaml` | Optional per-container services config |
| `/etc/pve/proxnix/containers/<vmid>/age_pubkey` | Container's age public key (written by `bootstrap.sh`) |
| `/etc/pve/proxnix/containers/<vmid>/dropins/` | Optional drop-in files (`.nix` or Quadlet) |
| `/etc/pve/priv/proxnix/containers/<vmid>/secrets/*.age` | Age-encrypted secret files stored in root-only `pmxcfs` |

---

## One-Time Setup on a Fresh Proxmox Host

### 1 — Install tooling

```bash
./install.sh
```

Run `install.sh` once on the first cluster node to seed `/etc/pve/proxnix` and `/etc/pve/priv/proxnix`, then run it on every other Proxmox node that may start managed containers. The installer always refreshes the per-node runtime files and only creates the shared `pmxcfs` content on the first node unless you pass `--force-shared`.

`yaml-to-nix.py` is installed to `/usr/local/lib/proxnix` on purpose: `/etc/pve` is backed by `pmxcfs`, which is fine for shared config data but a bad place for runnable scripts because executable metadata is not preserved there. Likewise, encrypted secrets live under `/etc/pve/priv`, because `pmxcfs` treats that subtree as root-only while the regular `/etc/pve` tree is readable by the Proxmox daemons in group `www-data`.

### 2 — Create a NixOS LXC container

Download the NixOS LXC template (community helper script or official):

```bash
# Example using the Proxmox community helper
bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/nixos.sh)"
```

Or import an existing NixOS rootfs tarball manually.

Create the container in the Proxmox WebUI or with `pct create`, then make sure:

- `ostype` is `nixos` so Proxmox includes `nixos.common.conf` automatically
- `features: nesting=1` is enabled if you plan to use the default Podman path
- the WebUI hostname, IP, gateway, DNS, and SSH public keys are set the way you want them

`proxnix` will mirror those WebUI-backed settings into generated Nix on first boot. It does **not** patch the creation template itself, and it does **not** keep a reusable root password in sync after creation.

### 3 — Optional per-container YAML config on the host

```bash
VMID=100
mkdir -p /etc/pve/proxnix/containers/$VMID

# Copy and edit the examples from this repo
cp proxmox.yaml /etc/pve/proxnix/containers/$VMID/proxmox.yaml
cp user.yaml    /etc/pve/proxnix/containers/$VMID/user.yaml

# Edit to match your network and desired services
$EDITOR /etc/pve/proxnix/containers/$VMID/proxmox.yaml
$EDITOR /etc/pve/proxnix/containers/$VMID/user.yaml
```

`proxmox.yaml` is optional if the PVE container config already has the right hostname, IP, gateway, DNS, and SSH public keys.

### 4 — First boot and first rebuild

```bash
pct start 100
```

On the first boot, the pre-start hook writes `base.nix`, `configuration.nix`, `chezmoi.nix`, `proxmox.nix`, and `user.nix` into the container before PID 1 starts. It also seeds a `proxnix-first-boot-rebuild.service` unit directly into the rootfs, so the first managed `nixos-rebuild switch` happens automatically during boot.

If you want to watch that first activation:

```bash
pct exec 100 -- journalctl -u proxnix-first-boot-rebuild -b
```

### 5 — Bootstrap secrets for the container

The age keypair is generated automatically by the `base.nix` activation script the first time the container boots. You only need to extract and store the public key on the host:

```bash
# Store your age (or SSH ed25519) public key once on the host
echo "age1..." > /etc/pve/proxnix/master_age_pubkey

# Extract the container's public key and store it on the host
./bootstrap.sh 100
```

`bootstrap.sh` prints the multi-recipient encryption command. Use it to encrypt each secret:

```bash
printf 'mysecretvalue' | age \
  -r "$(cat /etc/pve/proxnix/containers/100/age_pubkey)" \
  -r "$(cat /etc/pve/proxnix/master_age_pubkey)" \
  -o /etc/pve/priv/proxnix/containers/100/secrets/db_password.age
```

The `.age` files are pushed to `/etc/secrets/` inside the container on every `pct start`. Either recipient's private key can decrypt them — the container uses its own key, you use your master key for recovery or rotation.

Restart the container after adding the first secrets so the pre-start hook can push them and register them with Podman:

```bash
pct restart 100
```

From then on, every `pct start 100` will run the proxnix pre-start hook automatically and refresh generated config, secrets, and drop-ins from the host.

---

## Day-to-Day Workflow

**Change networking** (IP, hostname, DNS, SSH public keys):  
Edit the container in the Proxmox WebUI for fields PVE owns, or use `/etc/pve/proxnix/containers/<vmid>/proxmox.yaml` for extras such as `search_domain` / `ssh_keys`, then restart the container or run `nixos-rebuild switch` manually inside.

**Add/remove a Podman container**:  
Edit `/etc/pve/proxnix/containers/<vmid>/user.yaml` on the host, then restart the container or run `pct exec <vmid> -- nixos-rebuild switch`.

**Switch a container to native services** (Jellyfin, Immich):  
Set `podman: false` in `user.yaml` and declare the services under the `services:` key.  
See `user-native.yaml` for the Jellyfin + Immich example.

**Auto-rebuild on config push**:  
The `nixos-config-watcher.path` systemd unit inside every container watches  
`/etc/nixos/proxmox.nix` and `/etc/nixos/user.nix` for modifications.  
Any write to either file triggers `nixos-rebuild switch` automatically.

---

## Podman vs. Native Services

### Podman variant (`podman: true` in user.yaml)

Containers are declared via the built-in `virtualisation.oci-containers` NixOS module with the `podman` backend. Secrets are passed through Podman's `--secret` flag, and Docker-compat socket plus container DNS are enabled in `base.nix`. For the full Quadlet spec, use raw `.container` / `.network` / `.volume` drop-ins.

### Native variant (`podman: false` in user.yaml)

Use this for services that need `/dev/dri` hardware acceleration passthrough (Jellyfin, Immich). Podman is still available from `base.nix` unless you explicitly disable it in `configuration.nix`:

```nix
virtualisation.podman.enable = lib.mkForce false;
```

Hardware acceleration is enabled by adding the service user to the `render` and `video` groups and lifting `PrivateDevices`. Example: `user-native.yaml`.

---

## Secrets

### Model

```
Your workstation          Proxmox host              NixOS LXC container
─────────────────         ────────────────          ──────────────────────────
master private key        master_age_pubkey          /etc/age/identity.txt (600)
                          containers/100/            /etc/secrets/*.age    (400)
                            age_pubkey               /etc/secrets/.ids/    (UUID→name
                          /etc/pve/priv/proxnix/     mappings for shell driver)
                            containers/100/
                              secrets/
                                db_password.age      /run/<svc>-secrets/   (native
                                                      services — tmpfs, service
                                                      lifetime only)
```

The `/etc/age-secret-driver` script in `base.nix` implements all four mandatory Podman shell-driver commands (`list`, `lookup`, `store`, `delete`). The global driver config in `/etc/containers/containers.conf.d/age-secrets.conf` wires it up system-wide.

**Multi-recipient**: every `.age` file is encrypted to both the container's public key and your master key. The container only ever holds its own private key; your master key lives outside the cluster and is used for recovery and rotation.

### Per-container setup

```bash
# 1. Store your master public key on the Proxmox host (once)
echo "age1..." > /etc/pve/proxnix/master_age_pubkey   # age public key
# or an SSH ed25519 key works too:
# ssh-keyscan <your-host> | grep ed25519 > /etc/pve/proxnix/master_age_pubkey

# 2. Generate a keypair inside the new container (private key never leaves)
./bootstrap.sh 100

# 3. Encrypt a secret with multi-recipient
printf 'hunter2' | age \
  -r "$(cat /etc/pve/proxnix/containers/100/age_pubkey)" \
  -r "$(cat /etc/pve/proxnix/master_age_pubkey)" \
  -o /etc/pve/priv/proxnix/containers/100/secrets/db_password.age
```

The encrypted file is pushed to `/etc/secrets/db_password.age` inside the container on every `pct start`.

### Podman containers

Declare secrets in `user.yaml` with a name and injection target:

```yaml
secrets:
  - name: db_password
    type: env           # default — inject as environment variable
    target: DB_PASSWORD
  - name: tls_cert
    type: mount         # inject as a file
    target: /run/secrets/tls.crt
```

The pre-start hook registers a Podman shell-driver secret for each `.age` file. The global driver is configured in `base.nix` via `/etc/containers/containers.conf.d/age-secrets.conf` — no per-secret flags needed. When Podman needs a secret value, it calls `/etc/age-secret-driver lookup`, which decrypts the `.age` file on demand. **No plaintext file is ever written.**

### Native services (Immich, Jellyfin)

Declare secrets under the service in `user.yaml`:

```yaml
services:
  immich:
    secrets:
      - name: db_password
        path: /run/immich-secrets/db_password
```

`yaml-to-nix.py` emits a `systemd.tmpfiles` rule (creates the directory at boot) and an `ExecStartPre = ["+..."]` entry that decrypts the file as root just before the service starts. The decrypted file lives in a tmpfs path and is removed when the service stops.

Wire the path to the service option in a dropin `.nix` file:

```nix
# dropins/immich-secrets.nix
{ ... }: {
  services.immich.database.passwordFile = "/run/immich-secrets/db_password";
}
```

### proxnix-secrets — local CLI for secret management

`proxnix-secrets` runs on your workstation. It uses SSH to read/write `.age` files on the Proxmox host and your local age private key to decrypt. The private key never leaves your machine.

**Setup** (`~/.config/proxnix/config`):
```bash
PROXNIX_HOST=root@proxmox
PROXNIX_IDENTITY=~/.age/identity.txt   # or ~/.ssh/id_ed25519
PROXNIX_DIR=/etc/pve/proxnix           # default
PROXNIX_PRIV_DIR=/etc/pve/priv/proxnix # default secret store
```

**Commands**:
```bash
proxnix-secrets ls                     # list all secrets across all containers
proxnix-secrets ls 100                 # list secrets for vmid 100

proxnix-secrets set 100 db_password    # prompt for value, encrypt, push
printf 'hunter2' | proxnix-secrets set 100 db_password   # from stdin

proxnix-secrets get 100 db_password    # decrypt and print to stdout

proxnix-secrets rotate 100 db_password # re-encrypt with current keys
                                        # (use after adding a new container or
                                        #  rotating the master key)

proxnix-secrets rm 100 db_password     # delete
```

`set` and `rotate` both encrypt to two recipients — the container's `age_pubkey` and the host's `master_age_pubkey` — so either private key can decrypt.

After pushing new secrets, restart the container so the pre-start hook can register them with Podman:
```bash
pct restart 100
```

---

## Drop-in Files

For containers or config that doesn't fit the `user.yaml` schema, place files in the `dropins/` subdirectory of the container's config folder on the host:

```
/etc/pve/proxnix/containers/<vmid>/
├── proxmox.yaml
├── user.yaml
└── dropins/
    ├── extra.nix           ← arbitrary NixOS module fragment
    ├── myapp.container     ← native Podman Quadlet file (full syntax)
    ├── mynet.network       ← Quadlet network
    └── mydata.volume       ← Quadlet volume
```

**`.nix` files** are pushed to `/etc/nixos/dropins/` inside the container and auto-imported by `configuration.nix` on every rebuild. Use them for anything expressible in Nix: extra `virtualisation.oci-containers.*` blocks, additional systemd services, package overrides, etc.

**Quadlet files** (`.container`, `.volume`, `.network`, `.pod`, `.image`, `.build`) are pushed directly to `/etc/containers/systemd/` inside the container. They are picked up by Podman's `podman-system-generator` on `daemon-reload` — no NixOS rebuild needed. This path gives you the full Quadlet spec:

```ini
# dropins/myapp.container
[Container]
Image=ghcr.io/example/myapp:latest
PublishPort=9000:9000
Secret=api_key,type=env,target=API_KEY
AutoUpdate=registry
```

---

## Application Config Management (chezmoi)

Every LXC gets chezmoi pre-configured via `base.nix`. The setup is intentionally operator-driven — Nix prepares the environment, you control when config is applied.

### Responsibility split

| Layer | Manages |
|---|---|
| **Nix** | OS, packages, services, systemd units, containers — all declarative system state |
| **chezmoi** | Application config files only, under `/srv/config/<app>/` |
| **Backups** | All application state/data (databases, media, uploads) |

No overlap: chezmoi never touches anything under `/nix` or Nix-generated paths. Nix never manages anything under `/srv/config`.

### Layout

```
/srv/config/          ← chezmoi target (config files only)
  immich/
  jellyfin/
  adguard/

/var/lib/chezmoi/
  source/             ← chezmoi source state (back this up / point at a git repo)
```

### Operator workflow

```bash
cfg diff              # inspect drift between source state and /srv/config
cfg status            # summary of what would change
cfg apply             # reconcile /srv/config from source state
cfg apply --dry-run   # preview without writing
```

`cfg` is a thin wrapper installed on every LXC that runs chezmoi as the `configmgr` system user with the Nix-managed config at `/etc/chezmoi/chezmoi.toml`. All standard chezmoi subcommands work.

### Bootstrapping from a git repo

Set the option in a dropin `.nix` file or override in `base.nix`:

```nix
proxnix.chezmoi.bootstrapRepo = "git@github.com:you/chezmoi-configs.git";
```

On first boot the activation script clones the repo into the source directory (only if it's empty — safe to set on existing containers). Apply remains manual: review with `cfg diff` first.

### Wiring a service to its config

Configure the service in Nix to read from `/srv/config/<app>/`:

```nix
# dropins/immich-config.nix
{ ... }: {
  services.immich.configFilePath = "/srv/config/immich/config.json";
}
```

Services should not write back into `/srv/config`. If a service insists on mutating its config file, keep that file in a separate state directory and symlink or configure around it.

### Module options (`proxnix.chezmoi.*`)

| Option | Default | Description |
|---|---|---|
| `enable` | `true` (set in base.nix) | Enable/disable the whole setup |
| `configRoot` | `/srv/config` | chezmoi target directory |
| `sourceDir` | `/var/lib/chezmoi/source` | chezmoi source state |
| `user` | `configmgr` | System user that owns both directories |
| `bootstrapRepo` | `null` | Optional git repo to clone on first boot |

---

## Container Migration Paths

| You have | Tool to use |
|---|---|
| `docker-compose.yml` | [`compose2nix`](https://github.com/aksiksi/compose2nix) → paste output into a `.nix` drop-in |
| `podman run …` flags | [`podlet`](https://github.com/containers/podlet) `generate run -- <flags>` → `.container` drop-in |
| Existing `.container` Quadlet files | Drop them straight into `dropins/` — no conversion needed |
| Complex Nix-native Podman config | Use `virtualisation.oci-containers.*` in a `.nix` drop-in |

---

## Why `quadlet-nix` is not bundled

Older proxnix revisions fetched `quadlet-nix` from GitHub during evaluation. That made a brand-new container depend on outbound network access and an unpinned remote module before its first managed rebuild could succeed.

The default stack now avoids any remote module fetch on first boot:

- `user.yaml` Podman containers use built-in `virtualisation.oci-containers`
- raw Quadlet files still work through `dropins/*.container`
- if you want `quadlet-nix`, import and pin it yourself in a `.nix` drop-in

---

## SSH Access

`base.nix` enables OpenSSH with password auth disabled.

By default, proxnix will mirror SSH public keys from the PVE container config (`ssh-public-keys`) into `users.users.root.openssh.authorizedKeys.keys` during generation of `proxmox.nix`. You can also add keys explicitly in `/etc/pve/proxnix/containers/<vmid>/proxmox.yaml`:

```yaml
ssh_keys:
  - ssh-ed25519 AAAA... you@host
```

If you prefer to hard-code keys globally, bake them into `base.nix`:

```nix
users.users.root.openssh.authorizedKeys.keys = [
  "ssh-ed25519 AAAA... you@host"
];
```

---

## What proxnix can and cannot reconcile

**Can reconcile automatically**

- WebUI-backed hostname, IPv4/IPv6, gateway, DNS, and SSH public keys
- first managed activation of `/etc/nixos` on boot
- later config pushes through the pre-start hook plus `nixos-config-watcher`

**Cannot reconcile automatically**

- a broken or incomplete upstream NixOS LXC image/template
- the WebUI root password after creation; password SSH is disabled once `base.nix` is active
- security-sensitive CT feature flags such as `nesting=1`; proxnix warns, but does not silently change them for you

---

## Immich Unstable Package

When `unstable_package: true` is set for Immich in `user.yaml`, `yaml-to-nix.py` injects a `nixpkgs.config.packageOverrides` overlay that imports `nixos-unstable` for the `immich` package only. The rest of the system stays on the stable channel.

The overlay uses `fetchTarball` with a floating `nixos-unstable` ref. To pin it for reproducibility, replace the URL with a specific commit tarball and add a `sha256`:

```nix
fetchTarball {
  url = "https://github.com/NixOS/nixpkgs/archive/<commit>.tar.gz";
  sha256 = "sha256:...";
}
```
