# First Container

This page walks through the complete lifecycle for onboarding a new proxnix-managed NixOS container — from creation through health check.

> **Before you start:** Make sure you've completed all steps in [installation](installation.md), including the master key, shared keypair, admin password hash, and workstation config. Skipping those will cause subtle failures later.

## Overview

Here's what you'll do and why:

| Step | What | Why |
|------|------|-----|
| 1 | Create the CT in Proxmox | You need a NixOS LXC container |
| 2 | Create the host-side config directory | proxnix reads per-container config from here |
| 3 | Start the container | Triggers the pre-start and mount hooks that seed NixOS config |
| 4 | Bootstrap the NixOS channel | Fresh templates lack the root `nixos` channel needed for `nixos-rebuild` |
| 5 | Add the first secret (optional) | Demonstrates the secrets workflow |
| 6 | Verify health | Confirms everything is wired up correctly |

## 1. Create the CT in Proxmox

Use a NixOS Proxmox LXC template from Hydra and create the container in the Proxmox WebUI. If you prefer a guided shell flow, run `proxnix-create-lxc` on the Proxmox host; it validates the local proxnix install first, auto-detects the newest local NixOS template and a rootfs storage by default, creates the CT, and starts it for you.

**Resource requirements:**

- **RAM:** Set at least **2 GB**. Nix evaluation during bootstrap needs this much memory. You can lower it after the first successful rebuild.
- **Disk:** 8 GB minimum for a basic NixOS system; more for workloads
- **Features:** `proxnix-create-lxc` always enables `nesting=1` for NixOS CTs

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

## 2. Create the host-side container directory

proxnix looks for per-container configuration under `/etc/pve/proxnix/containers/<vmid>/`. Creating this directory is optional for a baseline container, but you'll need it for any customization.

For VMID `100`:

```bash
VMID=100
mkdir -p /etc/pve/proxnix/containers/$VMID/quadlets
```

### Optional files

| File | Purpose | When to use |
|------|---------|-------------|
| `proxmox.yaml` | Fields the WebUI can't express (e.g., `search_domain`, extra SSH keys) | When you need search domains or additional SSH keys |
| `user.yaml` | Native NixOS service definitions | When running services like Jellyfin, Immich, etc. |
| `dropins/*.nix` | Extra Nix configuration modules | When `user.yaml` isn't expressive enough |
| `dropins/*.service` | Attached systemd units | When you need custom services |
| `dropins/*.sh`, `*.py` | Scripts installed to `/usr/local/bin/` | For helper scripts |
| `quadlets/` | Podman Quadlet workload files | For container-first applications |

Example setup for a container with native services:

```bash
VMID=100
mkdir -p /etc/pve/proxnix/containers/$VMID/{quadlets,dropins}

cat > /etc/pve/proxnix/containers/$VMID/user.yaml << 'EOF'
runtime: native
services:
  jellyfin:
    enable: true
    hardware_acceleration: true
EOF
```

## 3. Start the container

```bash
pct start 100
```

At this point proxnix has already:

1. Run the **pre-start hook** — rendered the desired NixOS config into `/run/proxnix/100/`
2. Run the **mount hook** — copied that rendered config into the guest's `/etc/nixos/`
3. Installed the `proxnix-apply-config` service inside the guest
4. Generated the `proxnix-bootstrap.sh` script in `/root/`

The guest is now running with a seeded NixOS configuration, but it hasn't been built yet.

## 4. Bootstrap the NixOS channel

**Why:** The stock NixOS Proxmox template does not ship with the root `nixos` channel configured. Without it, `nixos-rebuild switch` cannot evaluate the configuration. This step is needed only once per container.

Enter the guest and run the generated bootstrap script:

```bash
pct enter 100
/root/proxnix-bootstrap.sh
```

This script:

1. Adds the NixOS channel matching the system's `stateVersion`
2. Runs `nix-channel --update`
3. Runs `nixos-rebuild switch` to apply the full proxnix-managed configuration
4. Records the applied config hash so future boots skip unnecessary rebuilds

**This will take several minutes** on the first run while Nix downloads and builds packages.

If the script fails, check:

- Is there enough RAM? (at least 2 GB)
- Does the container have internet access? (`ping 1.1.1.1`)
- Can DNS resolve? (`ping nixos.org`)

### What you should see when done

When you log in after bootstrap, you'll see:

- The proxnix MOTD with managed paths and useful commands
- A login summary showing IP, memory, disk, and Podman status

## 5. Add the first secret (optional)

proxnix now generates the per-container SSH-backed age keypair on the host automatically, so you can encrypt secrets to the container immediately:

```bash
# From your workstation (with proxnix-secrets configured)
proxnix-secrets set 100 mysecret
```

You'll be prompted to enter and confirm the secret value.

Restart the CT so the pre-start hook stages the encrypted secret store and the mount hook registers the secret with Podman:

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
  OK    container config dir present: /etc/pve/proxnix/containers/100
  OK    host age recipient present: /etc/pve/proxnix/containers/100/age_pubkey
  OK    guest container age identity present
  ...
  OK    guest file present: /etc/nixos/configuration.nix
  OK    guest file present: /etc/nixos/managed/base.nix
  ...
  OK    applied managed config hash matches current hash
  OK    host identity marker present

Summary: 0 fail(s), 0 warning(s)
```

## What to do next

- Add more services: see [native services](../workloads/native-services.md) or [Quadlet workloads](../workloads/quadlet-workloads.md)
- Learn about the configuration model: see [configuration model](../concepts/configuration-model.md)
- Set up more secrets: see [secrets](../concepts/secrets.md)
- Understand day-to-day operations: see [day-2 operations](../operations/day-2.md)
