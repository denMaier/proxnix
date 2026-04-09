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

  # Let Proxmox inject network config; we declare it explicitly in proxmox.nix
  proxmoxLXC.manageNetwork = false;

  # Nix daemon — no sandbox inside LXC (kernel namespacing not available)
  nix.settings.sandbox = false;

  # fstrim is a no-op in LXC and spams the journal
  services.fstrim.enable = false;

  # These mount units don't exist inside an unprivileged LXC container
  systemd.suppressedSystemUnits = [
    "dev-mqueue.mount"
    "sys-kernel-debug.mount"
    "sys-fs-fuse-connections.mount"
  ];

  # SSH: key-only root login, no passwords
  services.openssh = {
    enable = true;
    settings = {
      PasswordAuthentication = false;
      PermitRootLogin = "prohibit-password";
    };
  };

  # Local DNS caching via systemd-resolved
  services.resolved = {
    enable = true;
    dnssec = "false";
    extraConfig = ''
      Cache=yes
    '';
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
  # /etc/secrets/         — encrypted .age files pushed by the hookscript
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
  #   store  — write the UUID→name mapping (name is passed as stdin by hookscript)
  #   delete — remove the UUID→name mapping
  #
  # Registration convention (hookscript):
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
            "$SECRETS_DIR/${name}.age"
          ;;
        store)
          # Read name from stdin (our hookscript convention).
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
  # Any `podman secret create` call will use this driver by default so the
  # hookscript needs no per-secret --driver or --driver-opt flags.
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

  # Watch proxmox.nix and user.nix for changes; trigger nixos-rebuild switch
  # when either file is modified (e.g. after hookscript pushes new config).
  systemd.paths.nixos-config-watcher = {
    description = "Watch NixOS config files for changes";
    wantedBy = [ "multi-user.target" ];
    pathConfig = {
      PathModified = [
        "/etc/nixos/proxmox.nix"
        "/etc/nixos/user.nix"
      ];
    };
  };

  systemd.services.nixos-config-watcher = {
    description = "Rebuild NixOS on config file change";
    # Restart = "no" is correct for a oneshot triggered by a path unit
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "/run/current-system/sw/bin/nixos-rebuild switch";
      StandardOutput = "journal";
      StandardError = "journal";
    };
  };
}
