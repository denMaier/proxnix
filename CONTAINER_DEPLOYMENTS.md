# Container deployments for AI agents

This file is the quick reference for generating **Podman/Quadlet-based app configs** in proxnix.
Use it instead of scanning the repo.

## Decision rule

Choose a **container deployment** when the app should run as one or more Podman containers managed by Quadlet.

Keep container definitions in Quadlets. Native NixOS configuration belongs in `dropins/*.nix`.

## Where files go

For a target container VMID, host-side files usually live at:

- `/var/lib/proxnix/containers/<vmid>/quadlets/` for the main Quadlet workload tree
- `/var/lib/proxnix/containers/<vmid>/dropins/` for optional NixOS integration (`*.nix`), attached units, or small supporting scripts

Inside this repo, app templates should usually live under:

- `containers/<app>/quadlets/`
- optional `containers/<app>/dropins/` for NixOS `.nix` fragments
- `containers/<app>/README.md`
- app-specific config files beside the Quadlets when they should be mirrored into the guest

The existing Ente example follows this pattern.

## Accepted file types

These files in `quadlets/` are copied into `/etc/containers/systemd/` inside the guest, and the full `quadlets/` tree is mirrored to `/etc/proxnix/quadlets/`:

- `*.container`
- `*.volume`
- `*.network`
- `*.pod`
- `*.image`
- `*.build`

These files in `dropins/` are used for host-managed integration around the container workload:

- `*.nix` — imported into NixOS as extra modules
- `*.service` — attached under `/etc/systemd/system.attached/`
- `*.sh`, `*.py` — copied to `/usr/local/bin/`
- optional top-level Quadlet unit files — useful only for small supporting units

Use `dropins/` for integration glue. Put the main container workload and its nearby config in `quadlets/`.

## Recommended structure for a new app template

Create a folder like this in the repo:

```text
containers/<app>/
  README.md
  quadlets/
    app.container
    app.network
    app.volume
    app-config.yaml
  dropins/
    support.nix
  init.sh
```

Quadlet unit files go to `/etc/containers/systemd/`; nearby config files are mirrored into the guest's `/etc/proxnix/quadlets/` tree.

Use fully qualified image names in Quadlets. Prefer `docker.io/library/nginx:latest` over `nginx:latest`, and `docker.io/homeassistant/home-assistant:stable` over `homeassistant/home-assistant:stable`. This avoids registry-search ambiguity during rebuilds and restarts.

## How to handle config files

Because proxnix syncs the full `quadlets/` tree, app-owned config files can live beside the unit files on the host.

Use this pattern:

1. Keep declarative app config in `containers/<app>/quadlets/`.
2. Reference it under `/etc/proxnix/quadlets/...` in the guest.
3. Mount it into the container read-only.
4. Keep writable app state under `/var/lib/<app>/...` inside the guest.

Track guest-side drift with `jj -R /etc/proxnix/quadlets status` when needed.

Then in a Quadlet:

```ini
[Container]
Volume=/etc/proxnix/quadlets/myapp/config.yaml:/app/config.yaml:ro
```

## Secrets model

Secrets are managed on the Proxmox host with `proxnix-secrets`. They are stored in SOPS YAML stores, staged into the guest at boot, and exposed to Quadlets through Podman's shell secret driver.

Use secret names directly in Quadlets, for example:

```ini
[Container]
Secret=db_password,type=env,target=DB_PASSWORD
```

For file-style secrets inside a container, use plain `Secret=name,target=filename` and read `/run/secrets/<filename>` from the container process.

Do not hardcode secrets into Nix or checked-in YAML.

## Networking and runtime assumptions

Base proxnix enables Podman by default.
If the workload uses Podman, the Proxmox CT should have `features: nesting=1`.
The common pattern is:

- define a `*.network` file when the stack needs its own bridge network
- define `*.volume` files for persistent Podman volumes
- define a `*.pod` when multiple containers share ports and localhost behavior
- attach app containers either to the pod or the network directly
- add `AutoUpdate=registry` only when the image tag is intentionally tracking updates; check with `podman auto-update --dry-run` before applying

## What to generate for a new containerized app

When asked to generate a new container deployment, produce:

- `containers/<app>/README.md`
- `containers/<app>/quadlets/*.container`
- any needed `*.network`, `*.volume`, `*.pod`, `*.image`, or `*.build`
- optional `containers/<app>/dropins/*.nix` if the workload needs extra NixOS integration
- config files or helper scripts beside the Quadlets when they should be mirrored into `/etc/proxnix/quadlets/`
- a short secrets section listing the exact secret names to create

## What not to do

- Do not invent extra proxnix plumbing unless explicitly asked
- Do not assume files outside `quadlets/` are mirrored into the guest unless a drop-in type explicitly supports it
- Do not inline secrets into repo files
- Do not disable Podman unless the user explicitly wants a native-only container

## Minimal checklist

Before finishing a container app config, verify:

- the main workload is expressed as raw Quadlet files in `quadlets/`
- any extra NixOS integration lives in `dropins/*.nix`
- secrets are named and documented
- ports, pods, networks, and volumes are explicit
- the output matches the `containers/<app>/` template layout
