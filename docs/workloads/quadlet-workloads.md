# Quadlet Workloads

Use Quadlets for container-first applications, but manage them from guest Nix
config rather than through raw host-side Quadlet files.

The authoritative source still lives in your workstation-owned site repo under
`containers/<vmid>/dropins/*.nix`. proxnix publishes those modules to the node,
the guest imports them during activation, and systemd then realizes the
generated Podman units from guest-side Nix configuration.

## The model

proxnix is now opinionated here:

- proxnix itself does not import `quadlet-nix`
- proxnix does not stage raw `quadlets/` trees
- proxnix does not auto-enable Podman
- site-level shared imports belong in `site.nix`
- per-container activation belongs in `dropins/*.nix`

That keeps container workloads in the same Nix-driven model as native services.

## Shared import vs per-container activation

Import shared container modules once in `site.nix`, for example `quadlet-nix`.

Then activate actual workloads only in the containers that need them:

```nix
{ pkgs, ... }:

let
  siteRoot = "/var/lib/nginx-container-demo/html";
in {
  virtualisation.quadlet.enable = true;
  virtualisation.podman.enable = true;

  systemd.tmpfiles.rules = [
    "d ${siteRoot} 0755 root root -"
  ];

  proxnix.secrets.nginx_index_message.source = {
    scope = "container";
    name = "nginx_index_message";
  };

  proxnix._internal.configTemplateSources.nginx_index = pkgs.writeText "nginx-container-index.html" ''
      <!doctype html>
      <html>
        <body>
          <h1>{{ secrets.nginx_index_message }}</h1>
        </body>
      </html>
  '';

  proxnix.configs.nginx_index = {
    path = "${siteRoot}/index.html";
    owner = "root";
    group = "root";
    mode = "0644";
    secretValues = [ "nginx_index_message" ];
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

## Where files go

For a container VMID, proxnix-managed workload code lives under:

```text
/var/lib/proxnix/containers/<vmid>/dropins/
```

Use top-level `*.nix` files as the actual entrypoints.

## Workflow

1. Add or edit `containers/<vmid>/dropins/<workload>.nix` in your site repo.
2. If the workload needs a shared module layer such as `quadlet-nix`, import it
   from `site.nix`.
3. Publish with `proxnix-publish --vmid <vmid>`.
4. If you changed only config and did not touch secrets or identities, you can
   narrow that to `proxnix-publish --config-only --vmid <vmid>`.
5. Restart the container with `pct restart <vmid>`.
6. Verify inside the guest with `podman ps -a` and
   `systemctl status podman-<name>.service`.

## Config and state

Keep mutable application state under `/var/lib/<app>/...`.

For declarative config files, prefer Nix-native generation:

- `environment.etc`
- `pkgs.writeText`
- `pkgs.formats.*`
- `proxnix.configs.*`

If a container needs a file mounted in, generate that file from Nix and point
the container definition at the generated path.

## Secrets in container workloads

proxnix stages secrets into the guest and exposes helpers for consuming them.

Common patterns:

- materialize files with `proxnix.secrets.<name>.file`
- render config or mounted content with `proxnix.configs.<name>`
- run setup steps with `proxnix._internal.secrets.oneshot`
- use the Podman shell secret driver from guest config when the workload is Podman-based

The end-to-end exercise lab validates this path by materializing secret-backed
files, templates, oneshot consumers, and a Podman secret inside the guest, then
publishing the guest result at `http://<guest-ip>:18080/status.json`.

## Proxmox features

For proxnix-managed NixOS CTs, `nesting=1,keyctl=1` should already be enabled.
If you are working with a manually created container or a non-proxnix path, set them
explicitly in Proxmox. proxnix no longer infers or enables those settings from
host-side raw Quadlet files.
