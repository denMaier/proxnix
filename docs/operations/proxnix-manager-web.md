# Proxnix Manager Web

`Proxnix Manager Web` runs the same Manager frontend through a Bun HTTP server
instead of the Electrobun desktop shell. It is intended for Nix/NixOS
deployments where a reverse auth proxy handles login.

## Deployment model

The Manager code is split into explicit runtime boundaries:

- `backend/` - shared Manager capabilities: workstation bridge, Proxmox API,
  publish, doctor, secrets, git, and shared request handlers
- `frontend/` - browser UI used by both desktop and hosted web mode
- `desktop-shell/` - Electrobun window, file picker, open-path/editor
  integrations, and local RPC transport
- `webserver/` - Bun HTTP server, static frontend serving, and `/api/rpc`
  transport into the shared backend

The web server does not implement first-party login. Put a trusted auth proxy
in front of it for non-local access.

## Package

The workstation flake exports:

```bash
nix run github:denMaier/proxnix?dir=workstation#proxnix-manager-web -- --help
```

The package wraps the Bun web server with:

- `proxnix-workstation-cli` on `PATH`
- a Python environment suitable for the Manager bridge
- `PROXNIX_MANAGER_PYTHON` pointing at that Python
- `PROXNIX_MANAGER_PYTHONPATH` pointing at the Nix-provided workstation source
- runtime tools used by publish/secrets/git workflows: `sops`, `ssh`, `rsync`,
  and `git`

Run local-only web mode:

```bash
nix run github:denMaier/proxnix?dir=workstation#proxnix-manager-web
```

It binds to `127.0.0.1:4173` by default.

## NixOS module

The flake also exports:

```nix
inputs.proxnix.nixosModules.proxnix-manager-web
```

Deployment modes:

- `local` - loopback web server only; use for local access or your own proxy
- `reverse-proxy` - loopback web server plus nginx in front
- `direct` - exposes the Bun web server directly; requires
  `dangerouslyAllowDirect = true` and should only be used on isolated
  development networks

## Reverse auth proxy example

This example uses nginx `auth_request`. The auth service can be oauth2-proxy,
Authentik, Authelia, or any service that returns `2xx` for allowed requests and
`401` for unauthenticated requests.

```nix
{
  inputs.proxnix.url = "github:denMaier/proxnix?dir=workstation";

  outputs = { nixpkgs, proxnix, ... }: {
    nixosConfigurations.manager = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        proxnix.nixosModules.proxnix-manager-web

        ({ ... }: {
          proxnix.manager.web = {
            enable = true;
            deploymentMode = "reverse-proxy";

            # The hosted Manager stores its workstation config under:
            # /var/lib/proxnix-manager/.config/proxnix/config
            configHome = "/var/lib/proxnix-manager/.config";

            # Add the site repo so Manager can create bundles, update metadata,
            # stage git changes, and run publish workflows.
            extraReadWritePaths = [
              "/srv/proxnix-site"
            ];

            reverseProxy = {
              serverName = "proxnix.example.com";

              # Example oauth2-proxy endpoint. Authentik/Authelia deployments
              # can expose equivalent forward-auth or auth_request endpoints.
              authRequestUrl = "http://127.0.0.1:4180/oauth2/auth";
              signInUrl = "https://auth.example.com/oauth2/start?rd=$scheme://$host$request_uri";

              # Header trusted from the auth proxy and shown to Manager.
              trustedUserHeader = "X-Forwarded-User";
            };
          };
        })
      ];
    };
  };
}
```

The module configures:

- `systemd.services.proxnix-manager-web`
- a system user/group named `proxnix-manager`
- nginx virtual host for `reverse-proxy` mode
- nginx `auth_request` forwarding to `reverseProxy.authRequestUrl`
- trusted identity header propagation to the Manager

## Workstation config

Hosted Manager uses:

```text
/var/lib/proxnix-manager/.config/proxnix/config
```

Populate it with the same settings used by the CLI or desktop app, for example:

```bash
sudo install -d -o proxnix-manager -g proxnix-manager /var/lib/proxnix-manager/.config/proxnix
sudo install -o proxnix-manager -g proxnix-manager -m 0600 ./config \
  /var/lib/proxnix-manager/.config/proxnix/config
```

If the service should write to a site repo, make sure the configured service
user can read and write that repo and add the path to `extraReadWritePaths`.

## Security notes

- Keep the Bun web server on loopback unless you intentionally use `direct`.
- Prefer `deploymentMode = "reverse-proxy"` for shared access.
- Treat the auth proxy as the authentication boundary.
- Do not pass identity headers from untrusted clients directly to the Manager.
- Use TLS at the reverse proxy.
