{ config, lib, pkgs, ... }:

{
  imports = [
    <nixpkgs/nixos/modules/virtualisation/proxmox-lxc.nix>
  ];

  # Enable chezmoi-based application config management on every LXC.
  # Source state lives at /var/lib/chezmoi/source (persistent, back it up).
  # Config root is /srv/config/<app>/.
  # Override individual options in a dropin .nix if needed:
  #   proxnix.chezmoi.bootstrapRepo = "git@github.com:you/configs.git";
  proxnix.chezmoi.enable = true;

  # Shared cross-container operator baseline translated from the legacy
  # Debian/Ansible bootstrap: admin user, SSH hardening, journald caps,
  # timesync, swappiness, and a few convenience packages.
  proxnix.common = {
    enable = true;
    adminPasswordHashSecretName = "common_admin_password_hash";
    wheelNeedsPassword = true;
  };

  # Let Proxmox inject network config; we declare it explicitly in proxmox.nix
  proxmoxLXC.manageNetwork = false;

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

  # Run nixos-rebuild switch on boot whenever the managed config hash changes.
  # The prestart hook writes /etc/proxnix/current-config-hash before boot;
  # this service compares it to the last applied hash and rebuilds only on diff.
  systemd.services.proxnix-apply-config = {
    description = "Apply proxnix-managed NixOS configuration on hash change";
    after = [ "local-fs.target" "network-online.target" ];
    wants = [ "network-online.target" ];
    before = [ "multi-user.target" ];
    wantedBy = [ "multi-user.target" ];
    unitConfig = {
      ConditionPathExists = "/etc/nixos/configuration.nix";
    };
    environment = {
      # nixos-rebuild needs to find nixpkgs; point at root's channel tree which
      # the bootstrap script populates with nix-channel --add / --update.
      NIX_PATH = "nixpkgs=/nix/var/nix/profiles/per-user/root/channels/nixos:nixos-config=/etc/nixos/configuration.nix:/nix/var/nix/profiles/per-user/root/channels";
    };
    serviceConfig = {
      Type = "oneshot";
      StandardOutput = "journal";
      StandardError = "journal";
    };
    script = ''
      if [ ! -f /etc/proxnix/current-config-hash ]; then
        echo "proxnix-apply-config: no current-config-hash, skipping"
        exit 0
      fi
      current=$(cat /etc/proxnix/current-config-hash)
      applied=$(cat /etc/proxnix/applied-config-hash 2>/dev/null || true)
      if [ "$current" = "$applied" ]; then
        echo "proxnix-apply-config: config hash unchanged ($current)"
        exit 0
      fi
      echo "proxnix-apply-config: hash changed ($applied -> $current), rebuilding..."
      /run/current-system/sw/bin/nixos-rebuild switch
      tmp=$(mktemp /etc/proxnix/applied-config-hash.XXXXXX)
      printf "%s" "$current" > "$tmp"
      chmod 644 "$tmp"
      mv "$tmp" /etc/proxnix/applied-config-hash
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

  # age is used as the Podman shell-driver backend and for native-service
  # secret decryption.
  environment.systemPackages = [ pkgs.age ];

  # Ensure secret directories exist and generate an age keypair on first boot.
  # /etc/age/identity.txt — private key; never leaves the container
  # /etc/secrets/         — encrypted .age files pushed by the pre-start hook
  # /etc/secrets/.ids/    — UUID→name mappings written by the shell driver
  system.activationScripts.age-setup = ''
    mkdir -p /etc/age /etc/secrets /etc/secrets/.ids
    chmod 700 /etc/age /etc/secrets /etc/secrets/.ids
    if [ ! -f /etc/age/identity.txt ]; then
      ${pkgs.age}/bin/age-keygen -o /etc/age/identity.txt 2>/dev/null
      chmod 600 /etc/age/identity.txt
    fi
  '';

  # Unified Podman shell-driver dispatcher.
  #
  # All four mandatory commands route through this single script:
  #   list   — scan /etc/secrets/.ids/ and return JSON [{id,name},...}]
  #   lookup — decrypt /etc/secrets/<name>.age using the container's private key
  #   store  — write the UUID→name mapping (name is passed as stdin by pre-start hook)
  #   delete — remove the UUID→name mapping
  #
  # Registration convention (pre-start hook):
  #   printf '%s' "$secret_name" | podman secret create "$secret_name" -
  # Passing the name as stdin lets store() write the mapping without any
  # extra pct exec round-trips or flag-quoting concerns.
  environment.etc."age-secret-driver" = {
    mode = "0755";
    text = ''
      #!/bin/sh
      CMD="$1"
      IDS_DIR="/etc/secrets/.ids"
      IDENTITY="/etc/age/identity.txt"
      SECRETS_DIR="/etc/secrets"

      case "$CMD" in
        list)
          mkdir -p "$IDS_DIR"
          printf '['
          first=1
          for f in "$IDS_DIR"/*; do
            [ -f "$f" ] || continue
            uuid="$(basename "$f")"
            name="$(cat "$f")"
            [ "$first" = 1 ] && first=0 || printf ','
            printf '{"id":"%s","name":"%s"}' "$uuid" "$name"
          done
          printf ']\n'
          ;;
        lookup)
          name_file="$IDS_DIR/$SECRET_ID"
          if [ ! -f "$name_file" ]; then
            printf 'secret not found: %s\n' "$SECRET_ID" >&2
            exit 1
          fi
          name="$(cat "$name_file")"
          exec /run/current-system/sw/bin/age \
            --decrypt \
            --identity "$IDENTITY" \
            "$SECRETS_DIR/''${name}.age"
          ;;
        store)
          # Pre-start hook pre-populates .ids/ for all proxnix-managed secrets.
          # This branch is only reached for secrets created manually inside the
          # container via `podman secret create`; in that case the name is
          # passed as stdin.
          mkdir -p "$IDS_DIR"
          name="$(cat)"
          [ -n "$name" ] && printf '%s' "$name" > "$IDS_DIR/$SECRET_ID"
          ;;
        delete)
          rm -f "$IDS_DIR/$SECRET_ID"
          ;;
        *)
          printf 'unknown command: %s\n' "$CMD" >&2
          exit 1
          ;;
      esac
    '';
  };

  # Wire the age shell driver as the system-wide Podman secret backend.
  # The pre-start hook pre-registers all proxnix-managed secrets in Podman's
  # metadata (secrets.json) so `podman run --secret name` works immediately
  # after container start without any manual `podman secret create` step.
  environment.etc."containers/containers.conf.d/age-secrets.conf" = {
    mode = "0644";
    text = ''
      [secrets]
      driver = "shell"

      [secrets.opts]
      list   = "/etc/age-secret-driver list"
      lookup = "/etc/age-secret-driver lookup"
      store  = "/etc/age-secret-driver store"
      delete = "/etc/age-secret-driver delete"
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

  # ── Message of the day ────────────────────────────────────────────────────
  users.motd = ''

     ── proxnix ───────────────────────────────────────────────────────────────
      Config files   /etc/nixos/configuration.nix
                     /etc/nixos/managed/{base,common,chezmoi,proxmox,user}.nix
                     /etc/nixos/managed/dropins/*.nix
                     /etc/nixos/local.nix        optional local override

      Rebuild        automatic when managed config hash changes
                     nixos-rebuild switch
      Rebuild log    journalctl -u proxnix-apply-config -b

     Secrets        podman secret ls
                    podman run --secret NAME …
                    (managed on host via proxnix-secrets; auto-registered here)

     App config     cfg diff            review drift vs /srv/config/
                    cfg apply           apply source state to /srv/config/
                    cfg apply --dry-run preview without writing

      Containers     podman ps -a
                     podman logs -f NAME
                     systemctl status podman-NAME.service

     Nix            nix-collect-garbage -d          remove old generations
                    nixos-rebuild list-generations
    ──────────────────────────────────────────────────────────────────────────
  '';
}
