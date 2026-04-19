# Container deployments for AI agents

This file is the quick reference for generating container workloads in proxnix.
Use it instead of scanning the repo.

## Decision rule

Choose a container deployment when the app should run as one or more Podman
containers, but express that workload in guest Nix config.

Keep workload definitions in `dropins/*.nix`. Shared imports such as
`quadlet-nix` belong in `site.nix`, not in proxnix itself.

The source of truth lives in the workstation-owned site repo under
`containers/<vmid>/dropins/*.nix`. Publish it with `proxnix publish --vmid
<vmid>` before expecting the guest to converge on a restart.

## Where files go

For a target container VMID, host-side files usually live at:

- `/var/lib/proxnix/containers/<vmid>/dropins/` for NixOS modules and helper scripts

Inside this repo, app templates should usually live under:

- `containers/<app>/dropins/`
- `containers/<app>/README.md`

## Accepted file types

Files in `dropins/` are used for host-managed integration around the workload:

- `*.nix` — imported into NixOS as extra modules
- `*.service` — rejected; define the unit in `dropins/*.nix` instead
- `*.sh`, `*.py` — copied to `/var/lib/proxnix/runtime/bin/` and exposed on `PATH`

Top-level `dropins/*.nix` files are the entrypoints for native services and
container workloads alike.

## Recommended structure for a new app template

Create a folder like this in the repo:

```text
containers/<app>/
  README.md
  dropins/
    app.nix
    support.nix
```

If the application needs declarative config files, generate them from Nix using
`environment.etc`, `pkgs.writeText`, `pkgs.formats.*`, or a proxnix secret
template unit. Keep writable state under `/var/lib/<app>/...`.

Use fully qualified image names in container definitions. Prefer
`docker.io/library/nginx:latest` over `nginx:latest`.

Known-good minimal pattern:

```nix
{ pkgs, ... }:

let
  siteRoot = "/var/lib/nginx-container-demo/html";
in {
  virtualisation.podman.enable = true;
  virtualisation.quadlet.enable = true;

  systemd.tmpfiles.rules = [
    "d ${siteRoot} 0755 root root -"
  ];

  proxnix.secrets.templates.nginx-index = {
    source = pkgs.writeText "nginx-container-index.html" ''
      <!doctype html>
      <html>
        <body>
          <h1>__NGINX_INDEX_MESSAGE__</h1>
        </body>
      </html>
    '';
    destination = "${siteRoot}/index.html";
    owner = "root";
    group = "root";
    mode = "0644";
    substitutions = {
      "__NGINX_INDEX_MESSAGE__" = {
        secret = "nginx_index_message";
      };
    };
  };

  virtualisation.quadlet.containers.nginx = {
    autoStart = true;
    containerConfig = {
      image = "docker.io/library/nginx:latest";
      publishPorts = [ "127.0.0.1:8080:80" ];
      volumes = [ "${siteRoot}:/usr/share/nginx/html:ro" ];
    };
    serviceConfig.Restart = "always";
  };
}
```

Create the required secret before publishing:

```bash
proxnix secrets set <vmid> nginx_index_message
```

## Secrets model

Secrets are managed on the Proxmox host with `proxnix-secrets`. They are stored
in SOPS YAML stores and staged into the guest at boot.

For native services, materialize them with `proxnix.secrets.files`,
`proxnix.secrets.templates`, or `proxnix.secrets.oneshot`.

For Podman workloads managed from guest Nix, use the same secret primitives or
the proxnix Podman shell secret driver from guest config.

Do not hardcode secrets into Nix or checked-in YAML.

## Networking and runtime assumptions

proxnix does not auto-enable Podman. The guest module must do that explicitly,
for example with `virtualisation.podman.enable = true;`.

For proxnix-managed NixOS CTs created via `proxnix-create-lxc`, Proxmox
features `nesting=1,keyctl=1` are already set at creation time. If the
container was created manually or outside the normal proxnix path, treat those
features as an explicit Proxmox-side prerequisite.

## What to generate for a new containerized app

When asked to generate a new container deployment, produce:

- `containers/<app>/README.md`
- `containers/<app>/dropins/*.nix` for the actual workload
- any supporting attached units or scripts only when they are clearly needed
- a short secrets section listing the exact secret names to create

## What not to do

- Do not invent extra proxnix plumbing unless explicitly asked
- Do not inline secrets into repo files
- Do not assume proxnix will auto-enable Podman or import container modules

## Minimal checklist

Before finishing a container app config, verify:

- the workload is expressed in `dropins/*.nix`
- any shared module imports belong in `site.nix`
- secrets are named and documented
- ports, networks, volumes, and restart behavior are explicit
- the output matches the `containers/<app>/` template layout
