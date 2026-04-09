Agent Prompt: NixOS LXC Template System for Proxmox
Goal
Build a opinionated NixOS LXC configuration system for Proxmox homelab use. The output is a set of files that together form a complete, reproducible container management system. No need for maximum flexibility — optimize for clarity and our specific usecase.

Context & Architecture
We run NixOS LXC containers on Proxmox VE. Most services run in Podman containers declared in YAML. A few services (Jellyfin, Immich) run as native NixOS services directly in their LXC because they need /dev/dri hardware acceleration passthrough.
The system has these layers:
proxmox.nix          ← written by Proxmox hook, never touched by user
user.nix             ← generated from user.yaml by a conversion script
configuration.nix    ← imports base.nix + proxmox.nix + user.nix
base.nix             ← identical groundstate for all containers
Plus two source YAML files the user actually edits:
proxmox.yaml         ← hostname, static IP, gateway, DNS (Proxmox-managed)
user.yaml            ← which Podman containers or native services to run
And a conversion script that parses both YAMLs and writes the .nix files, plus a Proxmox hookscript that triggers the whole flow.

File Specifications
base.nix — groundstate, identical for every container, should include:

proxmox-lxc.nix module import
proxmoxLXC.manageNetwork = false (we manage it)
nix.settings.sandbox = false
services.fstrim.enable = false
Disable irrelevant systemd units (dev-mqueue.mount, sys-kernel-debug.mount, sys-fs-fuse-connections.mount)
services.openssh enabled, no password auth, root login via key only
services.resolved with caching enabled
Automatic Nix store garbage collection (weekly, delete generations older than 7 days)
virtualisation.podman enabled with dockerCompat = true and defaultNetwork.settings.dns_enabled = true
A systemd .path + .service unit pair watching /etc/nixos/proxmox.nix and /etc/nixos/user.nix for changes, triggering nixos-rebuild switch when either file is modified

configuration.nix (podman variant):
nix{ ... }: {
  imports = [ ./base.nix ./proxmox.nix ./user.nix ];
  system.stateVersion = "25.05";
}
configuration.nix (podman-less variant) — same but without podman in base, used for Jellyfin/Immich containers. Alternatively handled by a flag in user.yaml.
proxmox.nix — written programmatically, never hand-edited:
nix{ ... }: {
  networking.hostName = "...";
  networking.interfaces.eth0.ipv4.addresses = [{ address = "..."; prefixLength = ...; }];
  networking.defaultGateway = "...";
  networking.nameservers = [ "..." ];
}
proxmox.yaml — source of truth for Proxmox-managed config:
yamlhostname: example-container
ip: 192.0.2.10
prefix: 24
gateway: 192.0.2.1
dns:
  - 192.0.2.1
user.yaml (podman variant) — declares Podman containers as Quadlet units:
yamlpodman: true
containers:
  - name: myservice
    image: ghcr.io/example/myservice:latest
    ports:
      - "8080:8080"
    volumes:
      - "/var/lib/myservice:/data"
    env:
      TZ: Europe/Berlin
    restart: always
user.yaml (podman-less variant) — declares native NixOS services:
yamlpodman: false
services:
  jellyfin:
    enable: true
    hardware_acceleration: true   # adds user to render+video groups, disables PrivateDevices
  immich:
    enable: true
    media_location: /var/lib/immich
    hardware_acceleration: true
    unstable_package: true        # pulls immich from nixos-unstable overlay
yaml-to-nix.py — Python script that:

Reads proxmox.yaml → writes proxmox.nix
Reads user.yaml → writes user.nix with either Quadlet container declarations or native service declarations
Is idempotent — safe to run repeatedly

hookscript.sh — Proxmox hookscript placed in /var/lib/vz/snippets/, attached to containers via hookscript: local:snippets/hookscript.sh in the container config:

On pre-start phase: reads proxmox.yaml for this container, calls yaml-to-nix.py, pushes resulting proxmox.nix into the container rootfs via pct push
On post-start phase: if proxmox.nix was changed, pct exec a nixos-rebuild switch (the systemd path unit inside will also catch this, but an explicit rebuild on first boot / config change is safer)


Key constraints & opinions

Containers always use static IPs — no DHCP
SSH keys are baked into base.nix or passed via proxmox.yaml, never passwords
Podman containers are declared as systemd Quadlet units via virtualisation.oci-containers or native Quadlet, not Docker Compose
The user.nix for Podman containers should use virtualisation.oci-containers.containers NixOS module, not raw Quadlet files, for cleaner integration
Immich should use the nixos-unstable overlay pattern for its package only, rest of system stays on stable
Hardware acceleration for Jellyfin and Immich only needs users.users.<service>.extraGroups = ["render" "video"] and systemd.services.<service>.serviceConfig.PrivateDevices = lib.mkForce false — nothing more exotic
No Flakes — keep it simple, plain configuration.nix style
yaml-to-nix.py should use only Python stdlib, no third-party deps, so it runs on a stock Proxmox host (Debian-based, Python 3 available, no pip)


Reference Documentation
The agent should consult these before writing any code:
NixOS LXC on Proxmox:

https://nixos.wiki/wiki/Proxmox_Linux_Container
https://nixos.wiki/wiki/Proxmox_Virtual_Environment

NixOS module options referenced:

proxmox-lxc.nix module: https://github.com/NixOS/nixpkgs/blob/master/nixos/modules/virtualisation/proxmox-lxc.nix
virtualisation.oci-containers: https://search.nixos.org/options?query=virtualisation.oci-containers
services.jellyfin: https://search.nixos.org/options?query=services.jellyfin
services.immich: https://wiki.nixos.org/wiki/Immich
systemd.paths: https://search.nixos.org/options?query=systemd.paths
Nix garbage collection options: https://search.nixos.org/options?query=nix.gc

Proxmox hookscripts:

https://pve.proxmox.com/wiki/Hookscripts
pct man page for pct push and pct exec: https://pve.proxmox.com/pve-docs/pct.1.html

Immich unstable overlay pattern:

https://wiki.nixos.org/wiki/Immich (see "using unstable package" section)


Deliverables

base.nix
configuration.nix (with a comment showing how to disable podman for the podman-less variant)
proxmox.nix (example/template)
proxmox.yaml (example)
user.yaml (podman example)
user.yaml (podman-less example with Jellyfin + Immich)
yaml-to-nix.py
hookscript.sh

With a short README explaining where each file lives and the one-time setup steps on a fresh Proxmox host.
