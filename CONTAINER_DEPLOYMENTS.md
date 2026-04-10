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
- optional `/etc/pve/proxnix/containers/<vmid>/proxmox.yaml`
- optional `/etc/pve/proxnix/containers/<vmid>/user.yaml` only for native services, not containers

Inside this repo, app templates can live under:

- `containers/<app>/dropins/`
- `containers/<app>/README.md`
- app-specific config files at the root of that app folder

The existing Ente example follows this pattern.

## Accepted drop-in file types

These files in `dropins/` are copied into `/etc/containers/systemd/` inside the guest:

- `*.container`
- `*.volume`
- `*.network`
- `*.pod`
- `*.image`
- `*.build`

These files in `dropins/` are imported into NixOS as extra modules:

- `*.nix`

Use `*.nix` drop-ins for guest-side support files when Quadlets need config files to exist at stable paths such as `/etc/<app>/...`.

## Recommended structure for a new app template

Create a folder like this in the repo:

```text
containers/<app>/
  README.md
  dropins/
    app.container
    app.network
    app.volume
    support-files.nix
  app-config.yaml
  init.sh
```

Use the root files as the editable source of truth for humans.
Use a `dropins/*.nix` file to materialize them inside the guest if the Quadlets need mounted files.

## How to handle config files

Because proxnix only auto-links Quadlet file types from `dropins/`, arbitrary root files are **not** automatically copied into the guest.

Use this pattern:

1. Keep human-editable config files in `containers/<app>/`.
2. Add a `dropins/<app>-files.nix` module.
3. In that module, write files into `/etc/<app>/...` with `environment.etc`.
4. In Quadlets, mount those guest paths with `Volume=/etc/<app>/file:/path/in/container:ro`.

Example pattern:

```nix
{ ... }:
{
  environment.etc."myapp/config.yaml" = {
    mode = "0644";
    text = ''
      key: value
    '';
  };
}
```

Then in a Quadlet:

```ini
[Container]
Volume=/etc/myapp/config.yaml:/app/config.yaml:ro
```

## Secrets model

Secrets are managed on the Proxmox host with `proxnix-secrets` and arrive inside the guest as Podman secrets.

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
