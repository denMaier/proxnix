# Native NixOS deployments for AI agents

This file is the quick reference for generating native NixOS service configs in proxnix.
Use it instead of scanning the repo.

## Decision rule

Choose a native NixOS deployment when the app should run as a NixOS service from nixpkgs rather than as a Podman container.

In this mode, the primary input is one or more `dropins/*.nix` modules.

The source of truth lives in the workstation-owned site repo under
`containers/<vmid>/dropins/*.nix`. Publish it with `proxnix publish --vmid
<vmid>` before expecting the guest to converge on a restart.

## Core rule

Native-service config is pure Nix. If the app is containerized, use a
guest-side container module such as `quadlet-nix` from `dropins/*.nix`, not
raw host-side Quadlet files.

## Where files go

For a target container VMID, host-side files live at:

- `/var/lib/proxnix/containers/<vmid>/dropins/*.nix`
- `/var/lib/proxnix/containers/<vmid>/dropins/*.{sh,py}`

Inside the guest, proxnix generates and imports:

- `/var/lib/proxnix/config/managed/proxmox.nix` from the Proxmox CT config
- `/var/lib/proxnix/config/managed/dropins/*.nix` from host `dropins/*.nix`
- `/var/lib/proxnix/runtime/bin/*.{sh,py}` from host `dropins/*.{sh,py}`
- `/etc/nixos/local.nix` as an optional guest-only escape hatch

## Minimal shape

```nix
{ ... }: {
  services.nginx.enable = true;
}
```

## How secrets work for native services

Native service secrets are stored on the host with `proxnix-secrets`.
At runtime, proxnix materializes them from the compiled per-container secret
store according to the lifecycle you declare.

The guest identity and compiled secret store themselves live under
`/var/lib/proxnix/secrets/` as `root:root` `0600` regular files.

Your generated config must then point the service at that file path in a `dropins/*.nix` module.

Example pattern:

```nix
{ pkgs, ... }: {
  proxnix.secrets.nginx_index_message.source = {
    scope = "container";
    name = "nginx_index_message";
  };

  proxnix._internal.configTemplateSources.nginx_index = pkgs.writeText "nginx-index.html" ''
      <!doctype html>
      <html>
        <body>
          <h1>{{ secrets.nginx_index_message }}</h1>
        </body>
      </html>
  '';

  proxnix.configs.nginx_index = {
    service = "nginx";
    path = "/var/lib/nginx-demo/www/index.html";
    owner = "root";
    group = "root";
    mode = "0644";
    secretValues = [ "nginx_index_message" ];
  };

  services.nginx = {
    enable = true;
    virtualHosts."proxnix-native" = {
      default = true;
      listen = [{ addr = "0.0.0.0"; port = 8080; }];
      root = "/var/lib/nginx-demo/www";
      locations."/".tryFiles = "$uri $uri/ /index.html";
    };
  };

  networking.firewall.allowedTCPPorts = [ 8080 ];

  systemd.tmpfiles.rules = [
    "d /var/lib/nginx-demo/www 0755 root root -"
  ];
}
```

NixOS enables the firewall by default, so proxnix-managed guests start with it
on unless you override it. Open only the ports you need with
`networking.firewall.allowedTCPPorts`, or disable the firewall broadly in
`site.nix` or per-container in `dropins/*.nix`.

## When to use `dropins/*.nix`

Use a Nix drop-in when:

- the service needs attributes outside `services.<name>.*`
- you need custom systemd settings
- you need `environment.etc`, tmpfiles, users, firewall, or package overrides
- you want host-managed config to stay pure Nix

`dropins/*.nix` files are imported automatically by `configuration.nix`.

## What to generate for a new native app

When asked to generate a native deployment, produce:

- one or more `dropins/<app>.nix` modules
- documented secret names if the service needs credentials
- service-specific file references using proxnix-managed secret paths

## What not to do

- Do not define containers in native-service drop-ins
- Do not hardcode secrets in Nix
- Do not reach for Quadlets if a clean NixOS module already exists and the user asked for a native deployment
- Do not treat `/etc/nixos/local.nix` as the place for host-managed generated config

## Minimal checklist

Before finishing a native app config, verify:

- every managed service has `enable = true;`
- service-specific settings live in `dropins/*.nix`
- secrets are declared through `proxnix.secrets.*` or `proxnix.configs.*` and consumed from the configured paths
- no container definitions are mixed into the native-service config
