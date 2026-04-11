{ config, lib, pkgs, ... }:

let
  jjPackage = if pkgs ? jujutsu then pkgs.jujutsu else pkgs.jj;
in {
  imports = [
    <nixpkgs/nixos/modules/virtualisation/proxmox-lxc.nix>
  ];

  # Shared cross-container operator baseline translated from the legacy
  # Debian/Ansible bootstrap: admin user, SSH hardening, journald caps,
  # timesync, swappiness, and a few convenience packages.
  proxnix.common = {
    enable = true;
    adminPasswordHashSecretName = "common_admin_password_hash";
    wheelNeedsPassword = true;
  };

  # Networking: let Proxmox own it.  manageNetwork=false enables
  # systemd-networkd, which picks up the IP/gateway/DNS that Proxmox injects
  # at container start.  IP changes in the Proxmox UI take effect on a plain
  # container restart — no NixOS rebuild required.
  proxmoxLXC.manageNetwork = false;

  # Hostname: NixOS owns it (set via proxmox.nix from the PVE conf hostname).
  # manageHostName=true prevents the proxmox-lxc module from doing
  # mkForce "" on networking.hostName, which would blank it if Proxmox
  # doesn't write /etc/hostname for NixOS containers (per the NixOS wiki).
  proxmoxLXC.manageHostName = true;

  # Nix daemon — no sandbox inside LXC (kernel namespacing not available)
  nix.settings.sandbox = false;

  # fstrim is a no-op in LXC and spams the journal
  services.fstrim.enable = false;

  # pct enter (lxc-attach) creates an interactive non-login shell, so PAM never
  # runs and /etc/set-environment is never sourced — PATH and NIX_PATH are bare.
  # Sourcing it from /etc/bashrc fixes this for every interactive bash session
  # without affecting normal SSH logins (double-sourcing is harmless).
  programs.bash.interactiveShellInit = ''
    [ -f /etc/set-environment ] && . /etc/set-environment
  '';

  # These mount units don't exist inside an unprivileged LXC container
  systemd.suppressedSystemUnits = [
    "dev-mqueue.mount"
    "sys-kernel-debug.mount"
    "sys-fs-fuse-connections.mount"
  ];

  # Local DNS caching via systemd-resolved
  services.resolved = {
    enable = true;
    dnssec = "false";
    extraConfig = ''
      Cache=yes
    '';
  };

  # ping defaults to cap_net_raw file capability, which requires CAP_SETFCAP to
  # set on the wrapper binary.  CAP_SETFCAP is not available in unprivileged LXC
  # containers, causing suid-sgid-wrappers.service to fail.  Use setuid instead.
  security.wrappers.ping = {
    source = lib.mkForce "${pkgs.iputils}/bin/ping";
    setuid = lib.mkForce true;
    owner = lib.mkForce "root";
    group = lib.mkForce "root";
    capabilities = lib.mkForce "";
  };

  # Weekly Nix store GC: remove generations older than 7 days
  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 7d";
  };

  # sops decrypts the staged YAML secret stores. age still provides the
  # per-container identity used by SOPS. jj tracks guest-local Quadlet config
  # under /etc/proxnix/quadlets.
  environment.systemPackages = [
    pkgs.age
    pkgs.sops
    pkgs.python3Minimal
    jjPackage
  ];

  # Ensure secret directories exist and generate an age keypair on first boot.
  # /etc/age/identity.txt — private key; never leaves the container
  # /etc/proxnix/secrets/ — staged SOPS YAML stores
  # /etc/secrets/.ids/    — UUID→name mappings written by the shell driver
  system.activationScripts.age-setup = ''
    mkdir -p /etc/age /etc/proxnix/secrets /etc/secrets /etc/secrets/.ids
    chmod 700 /etc/age /etc/proxnix/secrets /etc/secrets /etc/secrets/.ids
    if [ ! -f /etc/age/identity.txt ]; then
      ${pkgs.age}/bin/age-keygen -o /etc/age/identity.txt 2>/dev/null
      chmod 600 /etc/age/identity.txt
    fi
  '';

  system.activationScripts.proxnix-quadlet-jj = ''
    mkdir -p /etc/proxnix/quadlets
    if [ ! -d /etc/proxnix/quadlets/.jj ]; then
      ${jjPackage}/bin/jj git init --colocate /etc/proxnix/quadlets >/dev/null 2>&1 \
        || ${jjPackage}/bin/jj init --git /etc/proxnix/quadlets >/dev/null 2>&1 \
        || true
    fi
  '';

  # Wire the proxnix helper as the system-wide Podman secret backend.
  # The mount hook pre-registers all proxnix-managed secrets in Podman's
  # metadata (secrets.json) so `podman run --secret name` works immediately
  # after container start without any manual `podman secret create` step.
  environment.etc."containers/containers.conf.d/proxnix-secrets.conf" = {
    mode = "0644";
    text = ''
      [secrets]
      driver = "shell"

      [secrets.opts]
      list   = "/usr/local/bin/proxnix-secrets podman list"
      lookup = "/usr/local/bin/proxnix-secrets podman lookup"
      store  = "/usr/local/bin/proxnix-secrets podman store"
      delete = "/usr/local/bin/proxnix-secrets podman delete"
    '';
  };

  # Podman with Docker-compat socket and container DNS
  virtualisation.podman = {
    enable = true;
    dockerCompat = true;
    defaultNetwork.settings.dns_enabled = true;
  };

  # ── Bootstrap reminder ────────────────────────────────────────────────────
  # Shown at every login until bootstrap.sh has been run on the Proxmox host.
  # The pre-start hook writes /etc/secrets/.bootstrap_done once age_pubkey
  # exists for this container, at which point this block is silent.
  environment.etc."profile.d/proxnix-bootstrap-hint.sh" = {
    mode = "0644";
    text = ''
      if [ ! -f /etc/secrets/.bootstrap_done ]; then
        vmid="$(cat /etc/proxnix/vmid 2>/dev/null || true)"
        [ -n "$vmid" ] || vmid="$(hostname)"
        printf '\n  Bootstrap pending — run on the Proxmox host:\n'
        printf '    ./bootstrap.sh %s\n' "$vmid"
        printf '  Then restart the container to enable secrets.\n\n'
      fi
    '';
  };

  # ── Config-drift reminder ─────────────────────────────────────────────────
  # Shown at every login when the managed config pushed by the pre-start hook
  # differs from the last applied generation, so the operator knows the next
  # container restart will auto-apply it or that they can rebuild manually.
  environment.etc."profile.d/proxnix-rebuild-hint.sh" = {
    mode = "0644";
    text = ''
      _current="$(cat /etc/proxnix/current-config-hash 2>/dev/null || true)"
      _applied="$(cat /etc/proxnix/applied-config-hash 2>/dev/null || true)"
      if [ -n "$_current" ] && [ "$_current" != "$_applied" ]; then
        printf '\n  proxnix: managed config has changed — restart the container to auto-apply,\n'
        printf '           or run manually:\n'
        printf '    nixos-rebuild switch\n\n'
      fi
      unset _current _applied
    '';
  };

  # ── Message of the day ────────────────────────────────────────────────────
  users.motd = ''

     ── proxnix ───────────────────────────────────────────────────────────────
      Config files   /etc/nixos/configuration.nix
                     /etc/nixos/managed/{base,common,proxmox,user}.nix
                     /etc/nixos/managed/dropins/*.nix
                     /etc/nixos/local.nix        optional local override

     Quadlets       /etc/proxnix/quadlets         jj-tracked config files
                    /etc/containers/systemd       Quadlet unit files

      Rebuild        run manually when managed config hash changes:
                     nixos-rebuild switch

     Secrets        podman secret ls
                    podman run --secret NAME …
                    (managed on host via proxnix-secrets; auto-registered here)

     /etc drift     etc status          review guest-local /etc changes
                    etc diff            inspect pending diffs
                    etc commit -m ...   save an operator snapshot

      Containers     podman ps -a
                     podman logs -f NAME
                     systemctl status podman-NAME.service

     Nix            nix-collect-garbage -d          remove old generations
                    nixos-rebuild list-generations
    ──────────────────────────────────────────────────────────────────────────
  '';
}
