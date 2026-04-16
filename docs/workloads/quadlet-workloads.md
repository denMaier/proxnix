# Quadlet Workloads

Use Quadlets for container-first applications.

For new proxnix configs, prefer declaring Quadlets from `dropins/*.nix` via a
Nix Quadlet module. Raw files under `quadlets/` remain fully supported and are
the direct escape hatch when you want exact hand-written Quadlet text.

## Quick example

For VMID 100, create a simple nginx container:

```bash
mkdir -p /var/lib/proxnix/containers/100/quadlets

cat > /var/lib/proxnix/containers/100/quadlets/nginx.container << 'EOF'
[Container]
Image=docker.io/library/nginx:latest
PublishPort=8080:80

[Service]
Restart=always

[Install]
WantedBy=default.target
EOF

pct restart 100
```

## Where files go

For a container VMID, place workload files under:

```text
/var/lib/proxnix/containers/<vmid>/quadlets/
```

Supported unit types include:

- `*.container`
- `*.volume`
- `*.network`
- `*.pod`
- `*.image`
- `*.build`

`quadlets/` remains the raw-file path for container workloads. For new Nix-first
configs, prefer `dropins/*.nix` plus a Quadlet Nix module, and use `quadlets/`
when you want raw files or mirrored assets beside them. You can also place the
same unit types in `dropins/` when they are just a small supplement to an
otherwise native-service container.

## How proxnix maps them into the guest

The mount hook mirrors the workload in two ways:

| Source | Guest destination | Purpose |
|--------|------------------|---------|
| Top-level Quadlet unit files | `/etc/containers/systemd/` | Systemd generator input — these become actual services |
| Full `quadlets/` tree | `/etc/proxnix/quadlets/` | App config mirror |

That split is important:

- `/etc/containers/systemd/` is the actual systemd generator input
- `/etc/proxnix/quadlets/` is the host-managed config mirror

## Proxmox requirement: `nesting=1`

Podman-based workloads need `features: nesting=1` in the CT config.

```bash
pct set <vmid> --features nesting=1
```

`proxnix-doctor` warns if Quadlet files are present but nesting is not enabled.

## Image naming rule

Use fully qualified image names, for example:

```text
docker.io/library/nginx:latest
docker.io/homeassistant/home-assistant:stable
```

That avoids registry resolution surprises during restarts and updates.

## State placement rule

Keep mutable application state under `/var/lib/<app>/...` rather than inside the mirrored Quadlet config tree.

Use `/etc/proxnix/quadlets/<app>/...` for declarative config files that should stay host-managed.

## Config files alongside Quadlets

Because proxnix syncs the full `quadlets/` tree, app-owned config files can live beside the unit files on the host:

```text
quadlets/
├── myapp.container
├── myapp.network
├── myapp.volume
└── myapp/
    └── config.yaml       ← app config, mirrored to /etc/proxnix/quadlets/myapp/
```

Reference them in Quadlets:

```ini
[Container]
Volume=/etc/proxnix/quadlets/myapp/config.yaml:/app/config.yaml:ro
```

## Secrets in container workloads

Podman sees proxnix-managed secrets through the guest-side shell driver implemented by `/usr/local/bin/proxnix-secrets`.

Use secret names directly in Quadlets:

```ini
[Container]
# As environment variable
Secret=db_password,type=env,target=DB_PASSWORD

# As file secret
Secret=db_password,target=db_password
# → readable at /run/secrets/db_password
```

That means you manage the secret once on the host, then consume it from Quadlets without any manual `podman secret create` step.

## When Quadlets are absent

Podman is off by default in proxnix. Raw top-level Quadlet unit files cause the
pre-start hook to generate a small Nix drop-in that enables Podman for that
guest. Nix-authored Quadlet modules can also enable Podman directly.
