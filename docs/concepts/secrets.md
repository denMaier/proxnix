# Secrets

proxnix uses SOPS-encrypted secret stores plus SSH-backed age identities.

In the current model the **workstation owns all secret state**:

- encrypted secret stores
- encrypted private identities
- the master recovery key

The Proxmox host is only a **relay cache**. It stores encrypted secret stores, one shared plaintext host relay identity, and guest identities re-encrypted at rest for that host relay key so it can restage them into guests on every boot.

## Quick recipe

Per-container secret:

```bash
# 1. Create or update the local secret
proxnix-secrets set 120 db_password

# 2. Publish the updated relay cache
proxnix-publish

# 3. Restart the container so proxnix re-stages it
pct restart 120

# 4. Verify inside the guest
pct exec 120 -- proxnix-secrets get db_password
```

Shared secret:

```bash
proxnix-secrets set-shared common_admin_password_hash
proxnix-publish
pct restart 120
```

## Source-of-truth paths

Inside the workstation-owned site repo:

| Store | Path |
|-------|------|
| Host relay identity | `private/host_relay_identity.sops.json` |
| Shared guest identity | `private/shared_age_identity.sops.json` |
| Shared secret store | `private/shared/secrets.sops.yaml` |
| Per-container identity | `private/containers/<vmid>/age_identity.sops.json` |
| Per-container secret store | `private/containers/<vmid>/secrets.sops.yaml` |

The identity files in the workstation site repo are encrypted to the master recovery key only, except for the host-side published copies of guest identities, which are re-encrypted to both the host relay key and the master recovery key.

The secret stores are encrypted to:

- the relevant relay identity public key
- the master recovery key

That means the workstation can always decrypt via the master key, and the guest can decrypt with the staged relay identity.

## Recipients model

### Per-container store recipients

When you run:

```bash
proxnix-secrets set <vmid> <name>
```

proxnix:

1. ensures `private/containers/<vmid>/age_identity.sops.json` exists
2. derives the container public key locally from that encrypted identity
3. encrypts `private/containers/<vmid>/secrets.sops.yaml` to:
   - the container public key
   - the master public key derived from `PROXNIX_MASTER_IDENTITY`

### Shared store recipients

When you run:

```bash
proxnix-secrets set-shared <name>
```

proxnix:

1. ensures `private/shared_age_identity.sops.json` exists
2. derives the shared public key locally
3. encrypts `private/shared/secrets.sops.yaml` to:
   - the shared public key
   - the master public key

## Publish flow

`proxnix-publish` builds a temporary relay tree locally, decrypts the shared host relay key, re-encrypts guest identities for both that host relay key and the master recovery key, and syncs the result to the host:

```text
Workstation source of truth                        Host relay cache
──────────────────────────                        ────────────────
private/host_relay_identity.sops.json ───────►    /etc/proxnix/host_relay_identity
private/shared/secrets.sops.yaml        ─────►    /var/lib/proxnix/private/shared/secrets.sops.yaml
private/shared_age_identity.sops.json   ─────►    /var/lib/proxnix/private/shared_age_identity.sops.json

private/containers/<vmid>/
  age_identity.sops.json                ─────►    /var/lib/proxnix/private/containers/<vmid>/age_identity.sops.json
  secrets.sops.yaml                     ─────►    /var/lib/proxnix/private/containers/<vmid>/secrets.sops.yaml
```

That means each Proxmox host persistently stores only one plaintext relay key. Guest identities remain encrypted at rest on the host and are decrypted only transiently during pre-start staging. In practice, root on the Proxmox host is still the trust boundary for secret relay.

## How secrets reach the guest

```text
Host relay cache                           Guest
────────────────                           ─────
/var/lib/proxnix/private/
/etc/proxnix/host_relay_identity  (used on host only during pre-start)
/var/lib/proxnix/private/
  shared/secrets.sops.yaml        ──►      /etc/proxnix/secrets/shared.sops.yaml
  containers/<vmid>/secrets.sops.yaml ─►   /etc/proxnix/secrets/container.sops.yaml
  shared_age_identity.sops.json   ──►      /etc/proxnix/secrets/shared_identity
  containers/<vmid>/age_identity.sops.json ─► /etc/proxnix/secrets/identity

                                          /etc/proxnix/secrets/ssh-keys.txt
                                            (combined container + shared identities)
```

The pre-start hook stages the relay cache into `/run/proxnix/<vmid>/`.

During that step, the host uses `/etc/proxnix/host_relay_identity` to decrypt the host-relay-encrypted guest identity files into transient plaintext under `/run/proxnix/<vmid>/keys/`.

The mount hook copies the encrypted stores and those staged guest identities into the guest.

The guest activation script combines the container identity and shared identity into `/etc/proxnix/secrets/ssh-keys.txt`, which SOPS uses for decryption.

If a relay-encrypted guest identity is absent on the host, proxnix stages nothing for that scope. That simply means that scope has no secrets available.

## Guest helper

Inside the guest, `/usr/local/bin/proxnix-secrets` provides these read-oriented commands:

```bash
proxnix-secrets ls
proxnix-secrets ls-shared
proxnix-secrets get <name>
proxnix-secrets get-shared <name>
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

Your service config must still point at that file path.

## Podman secrets

For Podman workloads, proxnix uses a shell-based secret driver.

The mount hook reconciles visible proxnix secret names into Podman's `secrets.json`, which lets Quadlet workloads consume proxnix-managed secrets without a manual `podman secret create`.

```ini
[Container]
Secret=db_password,type=env,target=DB_PASSWORD
Secret=db_password,target=db_password
```

## Built-in shared secrets

proxnix uses these shared secret names by default:

| Secret name | Purpose |
|-------------|---------|
| `common_admin_password_hash` | Shadow-compatible password hash for the admin user |

## Rotation

Use:

```bash
proxnix-secrets rotate <vmid>
proxnix-secrets rotate-shared
```

That re-encrypts the existing store to the currently configured recipients. Use it after replacing an identity or the master key.
