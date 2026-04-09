# proxNix — NixOS LXC Template System for Proxmox

Opinionated, reproducible NixOS containers on Proxmox VE.  
No Flakes, stdlib-only tooling, static IPs, key-only SSH.

---

## File Map

### Inside every NixOS LXC container (`/etc/nixos/`)

| File | Who touches it | Purpose |
|------|---------------|---------|
| `base.nix` | You (once, at template creation) | Identical groundstate for all containers |
| `configuration.nix` | You (once, at template creation) | Top-level imports + `stateVersion` |
| `proxmox.nix` | Hookscript (auto-generated) | Networking: hostname, IP, gateway, DNS |
| `user.nix` | Hookscript (auto-generated) | Services: Podman containers or native NixOS services |

### On the Proxmox host

| Path | Purpose |
|------|---------|
| `/etc/nixos-lxc/master_age_pubkey` | Your age (or SSH) public key — used in multi-recipient encryption |
| `/etc/nixos-lxc/yaml-to-nix.py` | Converts YAML → Nix; stdlib Python 3, no pip deps |
| `/etc/nixos-lxc/containers/<vmid>/proxmox.yaml` | Network config you edit per container |
| `/etc/nixos-lxc/containers/<vmid>/user.yaml` | Service config you edit per container |
| `/etc/nixos-lxc/containers/<vmid>/age_pubkey` | Container's age public key (written by `bootstrap.sh`) |
| `/etc/nixos-lxc/containers/<vmid>/secrets/*.age` | Age-encrypted secret files |
| `/etc/nixos-lxc/containers/<vmid>/dropins/` | Optional drop-in files (`.nix` or Quadlet) |
| `/var/lib/vz/snippets/nixos-hookscript.sh` | Proxmox hookscript (auto-pushes config on start) |

---

## One-Time Setup on a Fresh Proxmox Host

### 1 — Install tooling

```bash
mkdir -p /etc/nixos-lxc
cp yaml-to-nix.py /etc/nixos-lxc/
cp hookscript.sh  /var/lib/vz/snippets/nixos-hookscript.sh
chmod +x /var/lib/vz/snippets/nixos-hookscript.sh
```

No Python packages to install — `yaml-to-nix.py` uses only stdlib.

### 2 — Create a NixOS LXC container

Download the NixOS LXC template (community helper script or official):

```bash
# Example using the Proxmox community helper
bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/nixos.sh)"
```

Or import an existing NixOS rootfs tarball manually.

### 3 — Install base config inside the new container

SSH in (or use `pct enter <vmid>`) and place the NixOS config files:

```bash
# From the repo root, push the base files into the container (replace 100 with your VMID)
pct push 100 base.nix          /etc/nixos/base.nix          --perms 0644
pct push 100 configuration.nix /etc/nixos/configuration.nix --perms 0644
```

Then do a first rebuild inside the container:

```bash
pct exec 100 -- nixos-rebuild switch
```

### 4 — Add per-container YAML config on the host

```bash
VMID=100
mkdir -p /etc/nixos-lxc/containers/$VMID

# Copy and edit the examples from this repo
cp proxmox.yaml /etc/nixos-lxc/containers/$VMID/proxmox.yaml
cp user.yaml    /etc/nixos-lxc/containers/$VMID/user.yaml

# Edit to match your network and desired services
$EDITOR /etc/nixos-lxc/containers/$VMID/proxmox.yaml
$EDITOR /etc/nixos-lxc/containers/$VMID/user.yaml
```

### 5 — Bootstrap secrets for the container

The age keypair is generated automatically by the `base.nix` activation script the first time the container boots. You only need to extract and store the public key on the host:

```bash
# Store your age (or SSH ed25519) public key once on the host
echo "age1..." > /etc/nixos-lxc/master_age_pubkey

# Extract the container's public key and store it on the host
./bootstrap.sh 100
```

`bootstrap.sh` prints the multi-recipient encryption command. Use it to encrypt each secret:

```bash
printf 'mysecretvalue' | age \
  -r "$(cat /etc/nixos-lxc/containers/100/age_pubkey)" \
  -r "$(cat /etc/nixos-lxc/master_age_pubkey)" \
  -o /etc/nixos-lxc/containers/100/secrets/db_password.age
```

The `.age` files are pushed to `/etc/secrets/` inside the container on every `pct start`. Either recipient's private key can decrypt them — the container uses its own key, you use your master key for recovery or rotation.

### 6 — Attach the hookscript to the container

```bash
pct set 100 -hookscript local:snippets/nixos-hookscript.sh
```

From now on, every `pct start 100` will:
1. **pre-start** — generate `.nix` files from YAML; push them and `.age` secret files into the container
2. **post-start** — push drop-ins; register Podman shell-driver secrets; run `nixos-rebuild switch`

---

## Day-to-Day Workflow

**Change networking** (IP, hostname, DNS):  
Edit `/etc/nixos-lxc/containers/<vmid>/proxmox.yaml` on the host, then restart the container or run `nixos-rebuild switch` manually inside.

**Add/remove a Podman container**:  
Edit `user.yaml` on the host, restart the container (or `pct exec <vmid> -- nixos-rebuild switch`).

**Switch a container to native services** (Jellyfin, Immich):  
Set `podman: false` in `user.yaml` and declare the services under the `services:` key.  
See `user-native.yaml` for the Jellyfin + Immich example.

**Auto-rebuild on config push**:  
The `nixos-config-watcher.path` systemd unit inside every container watches  
`/etc/nixos/proxmox.nix` and `/etc/nixos/user.nix` for modifications.  
Any write to either file triggers `nixos-rebuild switch` automatically —  
even if you push files manually with `pct push`.

---

## Podman vs. Native Services

### Podman variant (`podman: true` in user.yaml)

Containers are declared via the [`quadlet-nix`](https://github.com/SEIAROTg/quadlet-nix) NixOS module, which generates proper Podman Quadlet unit files from Nix attributes. Full Quadlet feature set is available including secrets, `AutoUpdate`, networks, volumes, and pods. Docker-compat socket and container DNS are enabled in `base.nix`.

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
                            secrets/                  mappings for shell driver)
                              db_password.age        /run/<svc>-secrets/   (native
                                                      services — tmpfs, service
                                                      lifetime only)
```

The `/etc/age-secret-driver` script in `base.nix` implements all four mandatory Podman shell-driver commands (`list`, `lookup`, `store`, `delete`). The global driver config in `/etc/containers/containers.conf.d/age-secrets.conf` wires it up system-wide.

**Multi-recipient**: every `.age` file is encrypted to both the container's public key and your master key. The container only ever holds its own private key; your master key lives outside the cluster and is used for recovery and rotation.

### Per-container setup

```bash
# 1. Store your master public key on the Proxmox host (once)
echo "age1..." > /etc/nixos-lxc/master_age_pubkey   # age public key
# or an SSH ed25519 key works too:
# ssh-keyscan <your-host> | grep ed25519 > /etc/nixos-lxc/master_age_pubkey

# 2. Generate a keypair inside the new container (private key never leaves)
./bootstrap.sh 100

# 3. Encrypt a secret with multi-recipient
printf 'hunter2' | age \
  -r "$(cat /etc/nixos-lxc/containers/100/age_pubkey)" \
  -r "$(cat /etc/nixos-lxc/master_age_pubkey)" \
  -o /etc/nixos-lxc/containers/100/secrets/db_password.age
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

The hookscript registers a Podman shell-driver secret for each `.age` file. The global driver is configured in `base.nix` via `/etc/containers/containers.conf.d/age-secrets.conf` — no per-secret flags needed. When Podman needs a secret value, it calls `/etc/age-secret-driver lookup`, which decrypts the `.age` file on demand. **No plaintext file is ever written.**

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
PROXNIX_DIR=/etc/nixos-lxc             # default
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

After pushing new secrets, restart the container or trigger the hookscript to register them with Podman:
```bash
pct start 100   # hookscript runs automatically on start
```

---

## Drop-in Files

For containers or config that doesn't fit the `user.yaml` schema, place files in the `dropins/` subdirectory of the container's config folder on the host:

```
/etc/nixos-lxc/containers/<vmid>/
├── proxmox.yaml
├── user.yaml
└── dropins/
    ├── extra.nix           ← arbitrary NixOS module fragment
    ├── myapp.container     ← native Podman Quadlet file (full syntax)
    ├── mynet.network       ← Quadlet network
    └── mydata.volume       ← Quadlet volume
```

**`.nix` files** are pushed to `/etc/nixos/dropins/` inside the container and auto-imported by `configuration.nix` on every rebuild. Use them for anything expressible in Nix: extra `virtualisation.quadlet` blocks with `rawConfig`, additional systemd services, package overrides, etc.

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
| Nix-native Quadlet (complex configs) | Use `virtualisation.quadlet.*` in a `.nix` drop-in (backed by quadlet-nix) |

---

## Pinning quadlet-nix

`configuration.nix` fetches `quadlet-nix` from `main` by default. For a stable homelab, pin it to a release:

```nix
quadletNix = builtins.fetchTarball {
  url = "https://github.com/SEIAROTg/quadlet-nix/archive/refs/tags/vX.Y.Z.tar.gz";
  sha256 = "sha256:...";
};
```

Run `nix-prefetch-url --unpack <url>` on the Proxmox host to get the sha256.

---

## SSH Access

`base.nix` enables OpenSSH with password auth disabled. Add your public key to `root`'s authorized_keys inside the container, or bake it into `base.nix`:

```nix
users.users.root.openssh.authorizedKeys.keys = [
  "ssh-ed25519 AAAA... you@host"
];
```

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
