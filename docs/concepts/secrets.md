# Secrets

proxnix uses provider-backed workstation secret retrieval plus SOPS-encrypted
runtime stores and SSH-backed age identities.

In the current model the **workstation owns all secret state**:

- source secrets retrieved from a configured workstation secret provider
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

## Workstation provider model

The workstation CLI does not require one specific source-secret backend.
`proxnix-secrets`, `proxnix-publish`, and `proxnix-doctor` talk to a
workstation secret provider, then compile the result into the same
`effective.sops.yaml` runtime artifact used by the host and guest.

That means:

- host and guest runtime behavior stays unchanged
- source secret storage can be swapped without changing publish semantics
- precedence rules stay owned by proxnix, not by the backend

Configure the provider with:

```bash
export PROXNIX_SECRET_PROVIDER=embedded-sops
```

Or an external helper:

```bash
export PROXNIX_SECRET_PROVIDER=exec
export PROXNIX_SECRET_PROVIDER_COMMAND='/path/to/proxnix-secret-provider-helper'
```

The same settings can live in the standard workstation config file:

```bash
# ~/.config/proxnix/config
PROXNIX_SECRET_PROVIDER='passhole'
PROXNIX_PASSHOLE_DATABASE='~/.local/share/passhole/proxnix.kdbx'
PROXNIX_PASSHOLE_PASSWORD_FILE='~/.config/proxnix/passhole-password'
```

Provider-specific values read from that file are forwarded to the configured
provider automatically.

That includes `PROXNIX_PYKEEPASS_AGENT_SOCKET`, so if your SSH agent is exposed
via a nonstandard socket path you can configure it directly in the proxnix
config file instead of exporting it in the shell.

Built-in named providers:

| Provider | Notes |
|----------|-------|
| `embedded-sops` | Default repo-backed source store |
| `pass` | Uses `pass` path hierarchy |
| `gopass` | Uses `gopass` path hierarchy |
| `passhole` | Uses `ph` against a KeePass database |
| `pykeepass` | Uses the Python `pykeepass` library directly |
| `onepassword` | Uses the 1Password Python SDK directly |
| `onepassword-cli` | Uses the 1Password CLI |
| `keepassxc` | Uses `keepassxc-cli` |
| `bitwarden` | Uses the Bitwarden Python SDK |
| `bitwarden-cli` | Uses Bitwarden Secrets Manager CLI |
| `exec` | Arbitrary helper implementing the proxnix JSON contract |

All built-in providers map proxnix scopes onto the same logical hierarchy:

- `shared`
- `groups/<group>`
- `containers/<vmid>`

Provider-specific configuration:

| Provider | Required / useful environment |
|----------|-------------------------------|
| `embedded-sops` | none beyond normal proxnix config |
| `pass` | `PROXNIX_PASS_STORE_DIR` optional |
| `gopass` | `PROXNIX_GOPASS_STORE_DIR` optional |
| `passhole` | `PROXNIX_PASSHOLE_DATABASE` or `PROXNIX_PASSHOLE_CONFIG`; optional `PROXNIX_PASSHOLE_KEYFILE`, `PROXNIX_PASSHOLE_PASSWORD`, `PROXNIX_PASSHOLE_PASSWORD_FILE`, `PROXNIX_PASSHOLE_NO_PASSWORD`, `PROXNIX_PASSHOLE_NO_CACHE`, `PROXNIX_PASSHOLE_CACHE_TIMEOUT` (defaults to `600` seconds) |
| `pykeepass` | `PROXNIX_PYKEEPASS_DATABASE`; optional `PROXNIX_PYKEEPASS_KEYFILE`, `PROXNIX_PYKEEPASS_PASSWORD`, `PROXNIX_PYKEEPASS_PASSWORD_FILE`, `PROXNIX_PYKEEPASS_NO_PASSWORD`, `PROXNIX_PYKEEPASS_AGENT_PUBLIC_KEY`, `PROXNIX_PYKEEPASS_AGENT_CONTEXT`, `PROXNIX_PYKEEPASS_AGENT_SOCKET`, `PROXNIX_PYKEEPASS_NO_CACHE`, `PROXNIX_PYKEEPASS_CACHE_TIMEOUT` (agent-derived passwords default to `600` seconds) |
| `onepassword` | `PROXNIX_1PASSWORD_VAULT`; `PROXNIX_1PASSWORD_SDK_AUTH` or `OP_SERVICE_ACCOUNT_TOKEN`; optional `PROXNIX_1PASSWORD_SDK_INTEGRATION_NAME`, `PROXNIX_1PASSWORD_SDK_INTEGRATION_VERSION` |
| `onepassword-cli` | `PROXNIX_1PASSWORD_VAULT`; optional `PROXNIX_1PASSWORD_ACCOUNT` |
| `keepassxc` | `PROXNIX_KEEPASSXC_DATABASE`; optional `PROXNIX_KEEPASSXC_PASSWORD_FILE`, `PROXNIX_KEEPASSXC_KEY_FILE`, `PROXNIX_KEEPASSXC_NO_PASSWORD` |
| `bitwarden` | `PROXNIX_BITWARDEN_ORGANIZATION_ID` or `ORGANIZATION_ID`; `PROXNIX_BITWARDEN_ACCESS_TOKEN` or `ACCESS_TOKEN`; optional `PROXNIX_BITWARDEN_STATE_FILE` or `STATE_FILE`, `PROXNIX_BITWARDEN_API_URL` or `API_URL`, `PROXNIX_BITWARDEN_IDENTITY_URL` or `IDENTITY_URL`, `PROXNIX_BITWARDEN_USER_AGENT` |
| `bitwarden-cli` | normal `bws` auth and environment |
| all named providers | optional `PROXNIX_SECRET_PATH_PREFIX` to replace `proxnix` |

For providers that expose both SDK and CLI implementations, proxnix uses one
canonical provider name for the SDK-backed adapter and the same name with
`-cli` for the CLI adapter.

`pykeepass` can derive the KeePass database password from an SSH agent-backed
`ssh-ed25519` key. In that mode:

- keep a static `PROXNIX_PYKEEPASS_KEYFILE` on disk
- set `PROXNIX_PYKEEPASS_AGENT_PUBLIC_KEY` to the public key exposed by the agent
- optionally set `PROXNIX_PYKEEPASS_AGENT_CONTEXT` if you want a stable context string other than the database filename
- set `PROXNIX_PYKEEPASS_AGENT_SOCKET` if the agent is not reachable via `SSH_AUTH_SOCK`

Agent-derived pykeepass passwords are cached locally for 10 minutes by default
to avoid repeated SSH agent signing prompts. Override with
`PROXNIX_PYKEEPASS_CACHE_TIMEOUT` or disable with `PROXNIX_PYKEEPASS_NO_CACHE=1`.

To print the exact derived password proxnix will use for bootstrap or recovery:

```bash
proxnix-secrets print-keepass-password
```

For `pykeepass`, the password source is selected in this order:

1. `PROXNIX_PYKEEPASS_NO_PASSWORD=1`
2. `PROXNIX_PYKEEPASS_PASSWORD`
3. `PROXNIX_PYKEEPASS_PASSWORD_FILE`
4. `PROXNIX_PYKEEPASS_AGENT_PUBLIC_KEY`
5. no password

These password modes are mutually exclusive in practice. `PROXNIX_PYKEEPASS_KEYFILE`
is orthogonal and can be combined with any one of them.

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

Identities always remain SOPS-backed inside the workstation-owned site repo.
Source secret stores live there only when `PROXNIX_SECRET_PROVIDER=embedded-sops`.

Embedded-SOPS paths:

| Store | Path |
|-------|------|
| Host relay identity | `private/host_relay_identity.sops.yaml` |
| Shared secret store | `private/shared/secrets.sops.yaml` |
| Group secret store | `private/groups/<group>/secrets.sops.yaml` |
| Per-container identity | `private/containers/<vmid>/age_identity.sops.yaml` |
| Per-container source store | `private/containers/<vmid>/secrets.sops.yaml` |
| Container group memberships | `containers/<vmid>/secret-groups.list` |

With any non-embedded provider, proxnix still uses:

- `private/host_relay_identity.sops.yaml`
- `private/containers/<vmid>/age_identity.sops.yaml`
- `containers/<vmid>/secret-groups.list`

Only the source secret retrieval moves out of the repo.

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

## Public guest model

For `dropins/*.nix`, the public API is:

```nix
proxnix.secrets.<name>
proxnix.configs.<name>
```

`proxnix.secrets.<name>` declares a secret source and one or more delivery
modes. `proxnix.configs.<name>` declares a rendered file that can reference
public secrets explicitly.

### Public secrets

Every public secret starts with a `source` block:

```nix
proxnix.secrets.db_password.source = {
  scope = "shared";
  name = "common_db_password";
};
```

Supported scopes:

- `container`
- `group`
- `shared`

When `scope = "group"`, set `group = "<group-name>"`. Today this is validated
at declaration time, but the runtime store is still merged before guest lookup,
so scope does not change the in-guest `proxnix-secrets get <name>` interface.

#### File delivery

Use `file` when a service needs a plaintext file path:

```nix
proxnix.secrets.db_password = {
  source = {
    scope = "shared";
    name = "common_db_password";
  };
  file = {
    owner = "root";
    group = "myapp";
    mode = "0640";
    restartUnits = [ "myapp.service" ];
  };
};
```

Then consume the resolved path with:

```nix
config.proxnix.secrets.db_password.file.path
```

Public file secrets are container-lifetime files. If you set `restartUnits` or
`reloadUnits`, proxnix rewrites the file during activation and then triggers
the listed systemd units.

#### Environment delivery

Use `env` when a service should receive the secret through an environment file:

```nix
proxnix.secrets.db_password = {
  source = {
    scope = "shared";
    name = "common_db_password";
  };
  env = {
    service = "myapp";
    variable = "DB_PASSWORD";
  };
};
```

proxnix generates a helper unit that runs before `myapp.service` and appends
the generated file to `EnvironmentFile=`.

#### Credential delivery

Use `credential` when a service supports native systemd credentials:

```nix
proxnix.secrets.db_password = {
  source = {
    scope = "shared";
    name = "common_db_password";
  };
  credential = {
    service = "myapp";
    name = "db-password";
  };
};
```

proxnix prepares the credential file before service start and appends it to
`LoadCredential=`.

`env` and `credential` bindings are refreshed before service start. They do not
currently trigger automatic restarts on secret rotation; a later service
restart picks up the new value.

### Public rendered configs

Use `proxnix.configs.<name>` when you want proxnix to render a file from a
template plus explicit secret or literal value references.

Template source registration:

```nix
proxnix._internal.configTemplateSources.myapp = pkgs.writeText "myapp.conf.in" ''
  password = {{ secrets.db_password }}
  mode = {{ values.mode }}
'';
```

Rendered config declaration:

```nix
proxnix.configs.myapp = {
  service = "myapp";
  path = "/var/lib/myapp/config.toml";
  owner = "myapp";
  group = "myapp";
  mode = "0600";
  secretValues = [ "db_password" ];
  values.mode = "production";
};
```

Then consume the resolved path with:

```nix
config.proxnix.configs.myapp.path
```

For managed configs, proxnix automatically restarts the owning service after
rewriting the file when `service = "..."` is set. You can add extra
`restartUnits` or `reloadUnits` as needed.

Use `createOnly = true` for mutable seed files that should be initialized once
and then left alone:

```nix
proxnix.configs.myapp = {
  service = "myapp";
  path = "/var/lib/myapp/config.toml";
  createOnly = true;
  owner = "myapp";
  group = "myapp";
  mode = "0600";
  secretValues = [ "db_password" ];
};
```

For `createOnly` configs, proxnix orders the seed unit before the owning
service and skips future rewrites once the file already exists. Because later
updates are intentionally skipped, `restartUnits` and `reloadUnits` are not
allowed there.

## Internal engine

The old low-level engine still exists under:

```nix
proxnix._internal.secrets.files
proxnix._internal.secrets.templates
proxnix._internal.secrets.oneshot
```

Treat that as internal plumbing. Use it only for low-level cases that the
public API does not model cleanly yet.

### One-shot consumers

Use `proxnix._internal.secrets.oneshot` when a secret should be consumed
transiently and not left behind as a managed plaintext file:

```nix
proxnix._internal.secrets.oneshot.example = {
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
proxnix-secrets rotate-group <group>
```

That re-encrypts the authoring stores to the currently configured recipients.
After rotating or changing any secret, run `proxnix-publish` again and restart
the affected containers.

## Initializing stores

```bash
proxnix-secrets init-host-relay
proxnix-secrets init-container <vmid>
proxnix-secrets init-shared
```

`init-host-relay` creates the shared relay key that Proxmox hosts use to decrypt
guest identities during staging. `init-container` creates a per-container
identity. `init-shared` creates the shared secret store. `set` creates guest
identities automatically when needed, so explicit init is only required for the
host relay key and for pre-seeding empty stores.
