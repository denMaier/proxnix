# Native Services

Use native services when the application maps well to an existing NixOS module.

The authoritative source lives in your workstation-owned site repo under
`containers/<vmid>/dropins/*.nix`. `proxnix-publish` syncs those files to the
node-local relay cache, and the guest imports them from
`/var/lib/proxnix/config/managed/dropins/` during activation.

## Quick example

```nix
{ ... }: {
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
}
```

Put that in your site repo at `containers/<vmid>/dropins/nginx.nix`, then:

```bash
proxnix-publish --vmid <vmid>
pct restart <vmid>
```

After the restart and guest rebuild, nginx is serving on guest port `8080`.

## Main input file

The main declaration format is `dropins/*.nix`.

Minimal shape:

```nix
{ ... }: {
  services.nginx.enable = true;
}
```

## Typical patterns

### Basic service options

```nix
{ ... }: {
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
}
```

### Static content from the Nix store

```nix
{ pkgs, ... }: {
  services.nginx = {
    enable = true;
    virtualHosts."proxnix-native" = {
      default = true;
      listen = [{ addr = "0.0.0.0"; port = 8080; }];
      root = pkgs.writeTextDir "index.html" ''
        proxnix nginx native ok
      '';
      locations."/".tryFiles = "$uri $uri/ /index.html";
    };
  };

  networking.firewall.allowedTCPPorts = [ 8080 ];
}
```

### Secrets

For native services, use the direct public API:

```nix
proxnix.secrets.<name>
proxnix.configs.<name>
```

Use `proxnix.secrets.<name>.file/env/credential` for secret delivery and
`proxnix.configs.<name>` for rendered files that reference declared public
secrets explicitly.

Example:

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

Use `proxnix.configs.*` when a native service should read a rendered
secret-backed file, and read the final path from
`config.proxnix.configs.<name>.path`. Use `proxnix.secrets.*.file.path` when
the service needs a raw secret file instead. Public configs restart their
owning service automatically after an update when `service = "..."` is set.

**Important:** You must also create the secret on the host:

```bash
proxnix-secrets set <vmid> nginx_index_message
```

## When to use `dropins/*.nix`

Use a Nix drop-in when:

- a service needs configuration outside `services.<name>.*`
- you need firewall rules, users, tmpfiles, or custom units
- you want to seed configuration files or wire secret paths into unrelated options

## Workflow

1. Add or edit `containers/<vmid>/dropins/<name>.nix` in your site repo.
2. Run `proxnix-publish --vmid <vmid>` from the workstation.
3. If you changed only config and did not touch secrets or identities, you can
   narrow that to `proxnix-publish --config-only --vmid <vmid>`.
4. Restart the container with `pct restart <vmid>`.
5. Verify inside the guest with `systemctl status <unit>` or from the host with
   `proxnix-doctor <vmid>`.

## Example pattern

The repository's `containers/nginx-native/` template shows a good native-service pattern:

- enable a standard NixOS module
- serve a predictable static site from a managed guest path
- render the served content with `proxnix.configs.*`
- restart nginx automatically when the managed config changes

## What not to do

- do not define container workloads in native-service drop-ins
- do not hardcode secrets into Nix
- do not put durable host-managed config into `/etc/nixos/local.nix`
