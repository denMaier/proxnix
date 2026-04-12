# Secrets

proxnix uses SOPS-encrypted YAML stores with age recipients.

It supports both shared secrets and per-container secrets, and it exposes them to both native services and Podman workloads.

## Quick recipe

If you just want to add a secret to a container and don't need the full explanation:

```bash
# 1. Make sure the container has been bootstrapped (age_pubkey exists)
./bootstrap-guest-secrets.sh <vmid>

# 2. Create a secret
proxnix-secrets set <vmid> my_secret_name

# 3. Restart so the guest gets the staged secret
pct restart <vmid>

# 4. Verify inside the guest
pct exec <vmid> -- proxnix-secrets get my_secret_name
```

For shared secrets (available in all containers):

```bash
proxnix-secrets set-shared my_shared_secret
pct restart <vmid>  # restart any container that needs it
```

## Host-side secret stores

The encrypted stores live under `/etc/pve/priv/proxnix/`.

| Store | Path | Encrypted to |
|-------|------|-------------|
| Shared | `/etc/pve/priv/proxnix/shared/secrets.sops.yaml` | shared pubkey + master pubkey |
| Per-container | `/etc/pve/priv/proxnix/containers/<vmid>/secrets.sops.yaml` | container pubkey + master pubkey |

## Recipients model

### Per-container store recipients

When you run:

```bash
proxnix-secrets set <vmid> <name>
```

proxnix encrypts to:

- the container's SSH public key used as an `age` recipient from `/etc/pve/proxnix/containers/<vmid>/age_pubkey`
- the cluster master recovery key from `/etc/pve/proxnix/master_age_pubkey`

### Shared store recipients

When you run:

```bash
proxnix-secrets set-shared <name>
```

proxnix encrypts to:

- the shared SSH public key used as an `age` recipient from `/etc/pve/proxnix/shared_age_pubkey`
- the cluster master recovery key

## Bootstrap flow

### 1. The guest creates its own identity

`base.nix` generates `/etc/proxnix/secrets/identity` on first boot if it does not already exist. The private key never leaves the container.

### 2. The host records the public key

Run:

```bash
./bootstrap-guest-secrets.sh <vmid>
```

This reads the public key from the running guest and stores it as `/etc/pve/proxnix/containers/<vmid>/age_pubkey`.

> **Prerequisite:** The container must have completed at least one boot with `base.nix` applied (i.e., the NixOS channel bootstrap must be done first).

### 3. The host encrypts the store

Use `proxnix-secrets` to create, read, rotate, or remove entries.

## How secrets reach the guest

```
Host                                    Guest
────                                    ─────
/etc/pve/priv/proxnix/                 
  shared/secrets.sops.yaml        ──►   /etc/proxnix/secrets/shared.sops.yaml
  containers/<vmid>/              
    secrets.sops.yaml             ──►   /etc/proxnix/secrets/container.sops.yaml
                                        
/etc/pve/priv/proxnix/                 
  shared_age_identity.txt         ──►   /etc/proxnix/secrets/shared_identity
                                        
(generated on guest)                    /etc/proxnix/secrets/identity
                                        
                                        /etc/proxnix/secrets/ssh-keys.txt
                                          (combined: guest identity + shared identity)
```

The pre-start hook stages the encrypted YAML files into `/run/proxnix/<vmid>/secrets/`.

The mount hook copies them into the guest and also deploys the shared SSH-backed age identity.

The guest activation script combines the guest identity and shared identity into a single SSH key file at `/etc/proxnix/secrets/ssh-keys.txt`, which is what SOPS uses to decrypt.

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

That re-encrypts the existing store to the currently configured recipients. Use this after replacing a container's SSH-backed age identity or the master key.
