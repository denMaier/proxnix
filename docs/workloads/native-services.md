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

Declare proxnix secrets through the unified `proxnix.secrets` API.

Example:

```nix
{ pkgs, ... }: {
  proxnix.secrets.templates.nginx-index = {
    source = pkgs.writeText "nginx-index.html" ''
      <!doctype html>
      <html>
        <body>
          <h1>__NGINX_INDEX_MESSAGE__</h1>
        </body>
      </html>
    '';
    destination = "/var/lib/nginx-demo/www/index.html";
    owner = "root";
    group = "root";
    mode = "0644";
    restartUnits = [ "nginx.service" ];
    substitutions = {
      "__NGINX_INDEX_MESSAGE__" = {
        secret = "nginx_index_message";
      };
    };
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

Use `proxnix.secrets.templates` when nginx should serve rendered content from a
secret-backed template. Use `lifecycle = "activation"` for files that should
survive service and container restarts. Use `lifecycle = "service"` with a
`/run/...` path and a single owning `service` when the secret should only exist
while that service is running.

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
- render the served content with `proxnix.secrets.templates`
- restart nginx automatically when the activation-time template changes

## What not to do

- do not define container workloads in native-service drop-ins
- do not hardcode secrets into Nix
- do not put durable host-managed config into `/etc/nixos/local.nix`
