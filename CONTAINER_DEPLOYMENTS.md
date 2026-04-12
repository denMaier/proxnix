# Container deployments for AI agents

This file is the quick reference for generating **Podman/Quadlet-based app configs** in proxnix.
Use it instead of scanning the repo.

## Decision rule

Choose a **container deployment** when the app should run as one or more Podman containers managed by Quadlet.

Do **not** put container definitions in `user.yaml`.
`user.yaml` only supports native NixOS services.

## Where files go

For a target container VMID, host-side files live at:

- `/etc/pve/proxnix/containers/<vmid>/dropins/`
- `/etc/pve/proxnix/containers/<vmid>/quadlets/`
- optional `/etc/pve/proxnix/containers/<vmid>/proxmox.yaml`
- optional `/etc/pve/proxnix/containers/<vmid>/user.yaml` only for native services, not containers

Inside this repo, app templates can live under:

- `containers/<app>/quadlets/`
- `containers/<app>/dropins/` for NixOS `.nix` fragments
- `containers/<app>/README.md`
- app-specific config files at the root of that app folder

The existing Ente example follows this pattern.

## Accepted drop-in file types

These files in `quadlets/` are copied directly into `/etc/containers/systemd/` inside the guest:

- `*.container`
- `*.volume`
- `*.network`
- `*.pod`
- `*.image`
- `*.build`

These files in `dropins/` are imported into NixOS as extra modules:

- `*.nix`

Use `*.nix` drop-ins only for NixOS integration. App-specific support config belongs beside the Quadlet unit files on the host and is mirrored into `/etc/proxnix/quadlets/` inside the guest.

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

Use the root files as the editable source of truth for humans. Quadlet unit files go to `/etc/containers/systemd/`; non-unit files are mirrored into the guest's `/etc/proxnix/quadlets/` config tree.

Use fully qualified image names in Quadlets. Prefer `docker.io/library/nginx:latest` over `nginx:latest`, and `docker.io/homeassistant/home-assistant:stable` over `homeassistant/home-assistant:stable`. This avoids registry-search ambiguity during rebuilds and restarts.

## How to handle config files

Because proxnix syncs the full `quadlets/` tree, app-owned config files can live beside the unit files on the host.

Use this pattern:

1. Keep human-editable config files in `containers/<app>/quadlets/`.
2. Reference app-owned runtime config under `/etc/proxnix/quadlets/<app>/` in the guest.
3. Track guest-side edits with `jj -R /etc/proxnix/quadlets status`.
4. In Quadlets, mount those guest paths with `Volume=/etc/proxnix/quadlets/<app>/file:/path/in/container:ro`.

For writable app state, default to `/var/lib/<app>/...` inside the guest. Use `/etc/<app>` for native service config and `/opt/<app>` only for manually managed binaries or assets. That mirrors the toolkit's service-user convention while keeping proxnix's host-side source of truth under `/etc/pve/proxnix/containers/<vmid>/`.

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
- `containers/<app>/dropins/*.container`
- any needed `*.network`, `*.volume`, `*.pod`
- `containers/<app>/dropins/<app>-files.nix` if mounted config files are needed
- root config files like YAML, TOML, or helper scripts in `containers/<app>/`
- a short secrets section listing exact secret names to create

## What not to do

- Do not put container workloads under `user.yaml`
- Do not invent extra proxnix plumbing unless explicitly asked
- Do not assume arbitrary non-dropin files are copied into the guest
- Do not inline secrets into repo files
- Do not disable Podman unless the user explicitly wants a native-only container

## Minimal checklist

Before finishing a container app config, verify:

- all containers are raw Quadlet files in `dropins/`
- mounted config files are created via a `*.nix` drop-in
- secrets are named and documented
- ports, pods, networks, and volumes are explicit
- the output matches the `containers/<app>/` template layout
