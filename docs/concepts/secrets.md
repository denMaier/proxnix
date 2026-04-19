# Secrets

proxnix uses SOPS-encrypted secret stores plus SSH-backed age identities.

In the current model the **workstation owns all secret state**:

- encrypted secret stores
- encrypted private identities
- the master recovery key

The Proxmox host is only a **relay cache**. It stores encrypted secret stores,
one plaintext host relay identity, and container identities re-encrypted at
rest for that host relay key so it can restage them into guests on every boot.

## Runtime model

Workstation authoring supports:

- per-container secrets
- shared secrets
- grouped secrets

At runtime, each guest receives one secret store with this precedence:

1. shared
2. selected groups from `containers/<vmid>/secret-groups.list`
3. container-local secrets

The guest decrypts that runtime store with its staged container identity.

## Quick recipe

Per-container secret:

```bash
proxnix-secrets set 120 db_password
proxnix-publish
pct restart 120
pct exec 120 -- proxnix-secrets get db_password
```

Shared secret:

```bash
proxnix-secrets set-shared common_admin_password_hash
proxnix-publish
pct restart 120
```

Grouped secret:

```bash
cat > containers/120/secret-groups.list <<'EOF'
storage
network
EOF

proxnix-secrets set-group storage s3_access_key
proxnix-publish
pct restart 120
pct exec 120 -- proxnix-secrets get s3_access_key
```

## Source-of-truth paths

Inside the workstation-owned site repo:

| Store | Path |
|-------|------|
| Host relay identity | `private/host_relay_identity.sops.yaml` |
| Shared secret store | `private/shared/secrets.sops.yaml` |
| Group secret store | `private/groups/<group>/secrets.sops.yaml` |
| Per-container identity | `private/containers/<vmid>/age_identity.sops.yaml` |
| Per-container source store | `private/containers/<vmid>/secrets.sops.yaml` |
| Container group memberships | `containers/<vmid>/secret-groups.list` |

## Publish flow

`proxnix-publish` builds a temporary relay tree locally, prepares one compiled
secret store per container, re-encrypts container identities for both the host
relay key and the master recovery key, and syncs the result to the host:

```text
Workstation source of truth                        Host relay cache
──────────────────────────                        ────────────────
private/host_relay_identity.sops.yaml ───────►    /etc/proxnix/host_relay_identity

private/containers/<vmid>/
  age_identity.sops.yaml                ─────►    /var/lib/proxnix/private/containers/<vmid>/age_identity.sops.yaml
  compiled secret store                 ─────►    /var/lib/proxnix/private/containers/<vmid>/effective.sops.yaml
```

That means each Proxmox host persistently stores only one plaintext relay key.
Container identities remain encrypted at rest on the host and are decrypted
only transiently during pre-start staging.

## How secrets reach the guest

```text
Host relay cache                           Guest
────────────────                           ─────
/etc/proxnix/host_relay_identity  (used on host only during pre-start)
/var/lib/proxnix/private/containers/<vmid>/effective.sops.yaml ─► /var/lib/proxnix/secrets/effective.sops.yaml
/var/lib/proxnix/private/containers/<vmid>/age_identity.sops.yaml ─► /var/lib/proxnix/secrets/identity
```

The pre-start hook stages these files on the host. The mount hook then copies
them into the guest as root-owned regular files with root-only permissions.

## Guest helper

Inside the guest, proxnix copies `proxnix-secrets` into
`/var/lib/proxnix/runtime/bin/` and exposes it on `PATH`. It provides these
read-oriented commands:

```bash
proxnix-secrets ls
proxnix-secrets get <name>
```

## Unified Nix API

For `dropins/*.nix`, proxnix exposes declarative helpers under
`proxnix.secrets`.

### Files

Use `proxnix.secrets.files` when a service needs a plaintext file at runtime.

Activation-lifetime file:

```nix
proxnix.secrets.files.db-password = {
  path = "/var/lib/myapp-secrets/db_password";
  owner = "root";
  group = "myapp";
  mode = "0640";
  restartUnits = [ "myapp.service" ];
};
```

This is materialized persistently and survives normal service restarts and full
container restarts.

Service-bound file:

```nix
proxnix.secrets.files.db-password = {
  lifecycle = "service";
  service = "myapp.service";
  path = "/run/myapp-secrets/db_password";
  owner = "root";
  group = "myapp";
  mode = "0640";
};
```

This is created when the owning service starts and removed when it stops.

### Templates

Use `proxnix.secrets.templates` when you want proxnix to render a file from a
template and secret placeholders.

Activation-lifetime template:

```nix
proxnix.secrets.templates.myapp = {
  source = ./config.toml;
  destination = "/var/lib/myapp/config.toml";
  owner = "myapp";
  group = "myapp";
  mode = "0600";
  restartUnits = [ "myapp.service" ];
  substitutions = {
    "__PASSWORD__" = { secret = "db_password"; };
  };
};
```

This is materialized persistently and survives normal service and container
restarts.

Service-bound template:

```nix
proxnix.secrets.templates.myapp = {
  lifecycle = "service";
  service = "myapp.service";
  source = ./config.toml;
  destination = "/run/myapp/config.toml";
  owner = "myapp";
  group = "myapp";
  mode = "0600";
  substitutions = {
    "__PASSWORD__" = { secret = "db_password"; };
  };
};
```

This is created when the owning service starts and removed when it stops.

Use `createOnly = true` for mutable seed files that should be initialized once
and then left alone.

### One-shot consumers

Use `proxnix.secrets.oneshot` when a secret should be consumed transiently and
not left behind as a managed plaintext file:

```nix
proxnix.secrets.oneshot.example = {
  unit = "proxnix-example-secret-init";
  secret = "common_admin_password_hash";
  wantedBy = [ "multi-user.target" ];
  script = ''
    hash="$(tr -d '\r\n' < "$PROXNIX_SECRET_FILE")"
    echo "secret length: ''${#hash}"
  '';
};
```

The fetched secret is exposed to the script as `PROXNIX_SECRET_FILE`. Set
`optional = true` when a missing secret should be treated as a no-op.

## Podman secrets

For Podman workloads, proxnix uses a shell-based secret driver.

The mount hook reconciles visible proxnix secret names into Podman's
`secrets.json`, which lets Podman workloads consume proxnix-managed secrets
without a manual `podman secret create`.

## Built-in shared secrets

proxnix uses these shared secret names by default at the authoring layer:

| Secret name | Purpose |
|-------------|---------|
| `common_admin_password_hash` | Shadow-compatible password hash for the admin user |

## Rotation

Use:

```bash
proxnix-secrets rotate <vmid>
proxnix-secrets rotate-shared
```

That re-encrypts the authoring stores to the currently configured recipients.
After rotating or changing any secret, run `proxnix-publish` again and restart
the affected containers.
