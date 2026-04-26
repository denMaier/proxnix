# First Container

This page walks through the complete lifecycle for onboarding a new proxnix-managed NixOS container — from creation through health check.

> **Before you start:** Make sure you've completed all steps in [installation](installation.md), including the workstation site repo, provider configuration, and publish workflow. Skipping those will cause subtle failures later.

## Overview

Here's what you'll do and why:

| Step | What | Why |
|------|------|-----|
| 1 | Create the CT in Proxmox | You need a NixOS LXC container |
| 2 | Create the workstation-side container directory | proxnix reads published per-container config from here |
| 3 | Start the container | Triggers the pre-start and mount hooks that seed NixOS config |
| 4 | Bootstrap the NixOS channel | Fresh templates lack the root `nixos` channel needed for `nixos-rebuild` |
| 5 | Add the first secret (optional) | Demonstrates the secrets workflow |
| 6 | Verify health | Confirms everything is wired up correctly |

## 1. Create the CT in Proxmox

Use a NixOS Proxmox LXC template from Hydra and create the container in the Proxmox WebUI. If you prefer a guided shell flow, run `proxnix-create-lxc` on the Proxmox host; it validates the local proxnix install first, auto-detects the newest local NixOS template and a rootfs storage by default, creates the CT, and starts it for you.

**Resource requirements:**

- **RAM:** Set at least **2 GB**. Nix evaluation during bootstrap needs this much memory. You can lower it after the first successful rebuild.
- **Disk:** 8 GB minimum for a basic NixOS system; more for workloads
- **Features:** for NixOS CTs created through proxnix, `nesting=1,keyctl=1` should be enabled

After creation, confirm that Proxmox recognized it as NixOS:

```bash
pct config <vmid> | grep ostype
```

Expected result:

```text
ostype: nixos
```

If the CT uses a generic tarball and the type was detected incorrectly, fix it:

```bash
pct set <vmid> --ostype nixos
```

> `ostype=nixos` matters because Proxmox automatically includes the proxnix LXC config snippets for NixOS containers. Without it, the hooks won't run.

## 2. Create the workstation-side container directory

proxnix looks for published per-container configuration under `/var/lib/proxnix/containers/<vmid>/`, but the source of truth now lives in your workstation-owned site repo. Creating this directory is optional for a baseline container, but you'll need it for any customization.

For VMID `100`:

```bash
VMID=100
mkdir -p ~/src/proxnix-site/containers/$VMID/dropins
```

### Optional files

| File | Purpose | When to use |
|------|---------|-------------|
| `dropins/*.nix` | Native NixOS service definitions, extra config modules, and the default place for Nix-authored container workloads | When running services like native nginx or guest-side Podman nginx |
| `dropins/*.sh`, `*.py` | Scripts copied to `/var/lib/proxnix/runtime/bin/` and exposed on `PATH` | For helper scripts |

Example setup for a container with a native nginx service and a secret-rendered
page:

```bash
VMID=100
mkdir -p ~/src/proxnix-site/containers/$VMID/dropins

cat > ~/src/proxnix-site/containers/$VMID/dropins/nginx.nix << 'EOF'
{ pkgs, ... }:

{
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

  systemd.tmpfiles.rules = [
    "d /var/lib/nginx-demo/www 0755 root root -"
  ];

  networking.firewall.allowedTCPPorts = [ 8080 ];
}
EOF
```

Create the secret, then publish the new relay state before starting the
container:

```bash
proxnix-secrets set "$VMID" nginx_index_message
```

NixOS enables the firewall by default, so opening `8080` explicitly is
expected. If you want to disable the firewall across the whole published site,
set `networking.firewall.enable = false;` in `site.nix`. If you want to
disable it only for this one guest, set the same option in this container's
`dropins/*.nix`.

```bash
proxnix-publish
```

## 3. Start the container

```bash
pct start 100
```

At this point proxnix has already:

1. Run the **pre-start hook** — rendered the desired NixOS config into `/run/proxnix/100/` and ran `proxnix-reconcile-build --vmid 100`
2. Run the **mount hook** — copied the rendered build input into `/var/lib/proxnix/build-input/` with `rsync`, copied root-only secret files into `/var/lib/proxnix/secrets/`, and ran `proxnix-reconcile-seed-offline --vmid 100 --rootfs <mounted-rootfs>`
3. Run the guest **`proxnix-boot-activate.service`** — activated the staged `next-system` and verified `/run/current-system`
4. Removed any legacy guest-side `proxnix-apply-config` service files

The Proxmox node owns builds and seeding. A stopped container activates the
preseeded closure during boot; a running container can still be reconciled
explicitly from the host.

## 4. Deploy the desired system

If the CT is already running and you want to force convergence immediately, run
the host reconciler for the CT:

```bash
proxnix-reconcile --vmid 100
```

The host evaluates the generated authority, builds the desired NixOS closure,
imports it into the running CT without guest networking, activates the exact
system path, and records status under `/var/lib/proxnix/status/100.json`. The
same per-VMID running-CT path is also available as `systemctl start
proxnix-reconcile@100.service`.

If the CT is already running the evaluated desired system, the command exits as
`noop-current` without building or importing. Otherwise the host builds the
desired closure from its local Nix store. Keeping a golden-template build warm
on each host should make most container builds reuse existing store paths, so
the normal path does not depend on cross-node closure upload.

If activation fails, check:

- Did the host build complete? (`proxnix-reconcile --status --vmid 100`)
- Did offline seeding complete in the mount hook logs?
- Did `proxnix-boot-activate.service` revert to `previous-system`?

You can test the copied build input manually inside the guest if needed:

```bash
pct enter 100
nixos-rebuild test -I nixos-config=/var/lib/proxnix/build-input/configuration.nix
```

### What you should see when done

When you log in after boot activation finishes, you'll see:

- The proxnix MOTD with managed paths and useful commands
- A login summary showing IP, memory, disk, and basic runtime status

## 5. Add the first secret (optional)

`proxnix-secrets` creates the per-container identity in the workstation site repo on demand, so you can encrypt secrets to the container immediately:

```bash
# From your workstation (with proxnix-secrets configured)
proxnix-secrets set 100 mysecret
proxnix-publish
```

You'll be prompted to enter and confirm the secret value. Restart the CT so the pre-start hook stages the updated relay cache and the mount hook registers the secret with Podman:

```bash
# From the Proxmox host
pct restart 100
```

Verify from inside the guest:

```bash
pct enter 100
proxnix-secrets ls
proxnix-secrets get mysecret
```

## 6. Verify health

From the Proxmox host:

```bash
proxnix-doctor 100
```

Expected output for a healthy container:

```text
[host]
  OK    /usr/share/lxc/config/nixos.common.conf present
  OK    /usr/share/lxc/config/nixos.userns.conf present
  OK    /usr/share/lxc/hooks/nixos-proxnix-prestart present
  OK    /usr/share/lxc/hooks/nixos-proxnix-mount present
  ...

[ct 100]
  OK    PVE config present: /etc/pve/lxc/100.conf
  OK    ostype=nixos
  OK    container config dir present: /var/lib/proxnix/containers/100
  OK    host relay encrypted container identity present: /var/lib/proxnix/private/containers/100/age_identity.sops.yaml
  OK    guest container age identity present
  OK    guest container age identity is a root-owned 0600 regular file
  ...
  OK    guest file present: /var/lib/proxnix/build-input/configuration.nix
  OK    guest file present: /var/lib/proxnix/build-input/managed/base.nix
  ...
  INFO  legacy managed config hash is informational because reconciler status exists

Summary: 0 fail(s), 0 warning(s)
```

## What to do next

- Add more services: see [native services](../workloads/native-services.md) or [Quadlet workloads](../workloads/quadlet-workloads.md)
- Learn about the configuration model: see [configuration model](../concepts/configuration-model.md)
- Set up more secrets: see [secrets](../concepts/secrets.md)
- Validate a full disposable end-to-end workflow: see [LXC exercise lab](../operations/lxc-exercise-lab.md)
- Understand day-to-day operations: see [day-2 operations](../operations/day-2.md)
