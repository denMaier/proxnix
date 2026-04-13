# Secrets

proxnix uses SOPS-encrypted YAML stores with age recipients.

It supports both shared secrets and per-container secrets, and it exposes them to both native services and Podman workloads.

## Quick recipe

If you just want to add a secret to a container and don't need the full explanation:

```bash
# 1. Create a secret
proxnix-secrets set <vmid> my_secret_name

# 2. Restart so the guest gets the staged secret
pct restart <vmid>

# 3. Verify inside the guest
pct exec <vmid> -- proxnix-secrets get my_secret_name
```

For shared secrets (available in all containers):

```bash
proxnix-secrets set-shared my_shared_secret
pct restart <vmid>  # restart any container that needs it
```

## Host-side secret stores

The encrypted stores live under `/var/lib/proxnix/private/`.

| Store | Path | Encrypted to |
|-------|------|-------------|
| Shared | `/var/lib/proxnix/private/shared/secrets.sops.yaml` | shared pubkey + master pubkey |
| Per-container | `/var/lib/proxnix/private/containers/<vmid>/secrets.sops.yaml` | container pubkey + master pubkey |

## Recipients model

### Per-container store recipients

When you run:

```bash
proxnix-secrets set <vmid> <name>
```

proxnix encrypts to:

- the container's SSH public key used as an `age` recipient from `/var/lib/proxnix/containers/<vmid>/age_pubkey`
- the node's master recovery key from `/var/lib/proxnix/master_age_pubkey`

### Shared store recipients

When you run:

```bash
proxnix-secrets set-shared <name>
```

proxnix encrypts to:

- the shared SSH public key used as an `age` recipient from `/var/lib/proxnix/shared_age_pubkey`
- the node's master recovery key

## Bootstrap flow

### 1. The host creates the per-container identity

When a container is created through `proxnix-create-lxc` — or on the first proxnix-managed start of a manual container — proxnix ensures a host-managed SSH-backed age keypair exists for that VMID.

That produces:

- `/var/lib/proxnix/containers/<vmid>/age_pubkey`
- `/var/lib/proxnix/private/containers/<vmid>/age_identity.txt`

### 2. The host encrypts the store

Use `proxnix-secrets` to create, read, rotate, or remove entries.

## How secrets reach the guest

```
Host                                    Guest
────                                    ─────
/var/lib/proxnix/private/                 
  shared/secrets.sops.yaml        ──►   /etc/proxnix/secrets/shared.sops.yaml
  containers/<vmid>/              
    secrets.sops.yaml             ──►   /etc/proxnix/secrets/container.sops.yaml
                                        
/var/lib/proxnix/private/                 
  shared_age_identity.txt         ──►   /etc/proxnix/secrets/shared_identity
                                        
/var/lib/proxnix/private/containers/<vmid>/
  age_identity.txt               ──►   /etc/proxnix/secrets/identity
                                        
                                        /etc/proxnix/secrets/ssh-keys.txt
                                          (combined: container identity + shared identity)
```

The pre-start hook stages the encrypted YAML files into `/run/proxnix/<vmid>/secrets/`.

The mount hook copies them into the guest and also deploys the per-container SSH-backed age identity plus the shared SSH-backed age identity.

The guest activation script combines the container identity and shared identity into a single SSH key file at `/etc/proxnix/secrets/ssh-keys.txt`, which is what SOPS uses to decrypt.

## Guest helper

Inside the guest, `/usr/local/bin/proxnix-secrets` provides these read-oriented commands:

```bash
proxnix-secrets ls           # list all visible secrets with source (shared/container)
proxnix-secrets ls-shared    # list only shared secrets
proxnix-secrets get <name>   # decrypt a secret (checks container store first, then shared)
proxnix-secrets get-shared <name>  # decrypt from shared store only
```

## Native service secrets

For native services, declare secrets in `user.yaml`.

Example:

```yaml
runtime: native
services:
  immich:
    enable: true
    secrets:
      - name: db_password
        path: /run/immich-secrets/db_password
```

`yaml-to-nix.py` emits:

- a tmpfiles rule to create `/run/<service>-secrets`
- `ExecStartPre` commands that decrypt each declared secret before the service starts

Your service config must still point at that file path. Example in a drop-in:

```nix
{ ... }: {
  services.immich.database.passwordFile = "/run/immich-secrets/db_password";
}
```

If `path` is omitted, proxnix defaults to `/run/<service>-secrets/<secret-name>`.

## Podman secrets

For Podman workloads, proxnix uses a shell-based secret driver.

The mount hook reconciles the visible secret names into Podman's `secrets.json` and maps stable secret IDs back to proxnix secret names.

That allows Quadlet workloads to consume proxnix-managed secrets without any manual `podman secret create` step:

```ini
[Container]
# As an environment variable
Secret=db_password,type=env,target=DB_PASSWORD

# As a file inside the container
Secret=db_password,target=db_password
# → readable at /run/secrets/db_password inside the container
```

## Built-in shared secrets

proxnix uses these shared secret names by default:

| Secret name | Purpose | Set during |
|-------------|---------|------------|
| `common_admin_password_hash` | Shadow-compatible password hash for the admin user | [Installation step 4](../getting-started/installation.md#step-4-set-the-admin-user-password-hash) |

## Rotation

Use:

```bash
proxnix-secrets rotate <vmid>
proxnix-secrets rotate-shared
```

That re-encrypts the existing store to the configured recipients. Use this after replacing a container's SSH-backed age identity or the master key.
