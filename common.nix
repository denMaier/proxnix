{ config, lib, pkgs, ... }:

let
  cfg = config.proxnix.common;
  inheritedRootAuthorizedKeys = config.users.users.root.openssh.authorizedKeys.keys or [];
  adminAuthorizedKeys =
    lib.unique (
      cfg.adminAuthorizedKeys
      ++ lib.optionals cfg.inheritRootAuthorizedKeys inheritedRootAuthorizedKeys
    );
in {

  options.proxnix.common = {

    enable = lib.mkEnableOption "shared proxnix LXC baseline";

    adminUser = lib.mkOption {
      type = lib.types.str;
      default = "admin";
      description = lib.mdDoc ''
        Primary operator account created on every proxnix-managed LXC.
      '';
    };

    adminUid = lib.mkOption {
      type = lib.types.int;
      default = 1000;
      description = lib.mdDoc ''
        UID assigned to the shared operator account.
      '';
    };

    adminShell = lib.mkOption {
      type = lib.types.package;
      default = pkgs.bashInteractive;
      description = lib.mdDoc ''
        Login shell package for the shared operator account.
      '';
    };

    adminExtraGroups = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "wheel" ];
      description = lib.mdDoc ''
        Extra groups for the shared operator account.
      '';
    };

    adminAuthorizedKeys = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [];
      description = lib.mdDoc ''
        Additional SSH public keys for the shared operator account.
      '';
    };

    inheritRootAuthorizedKeys = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = lib.mdDoc ''
        When enabled, reuse whatever SSH public keys proxnix already assigns to
        `root` (typically mirrored from the Proxmox container config) for the
        shared operator account as well.
      '';
    };

    adminPasswordHash = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = lib.mdDoc ''
        Optional hashed password for the shared operator account. When unset,
        the account is SSH-key-only and local password login stays locked.
      '';
    };

    adminPasswordHashSecretName = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = lib.mdDoc ''
        Optional shared proxnix secret name containing a shadow-compatible
        password hash for the shared operator account. The value is read from
        the staged SOPS-backed proxnix secret store on boot before being
        applied.
      '';
    };

    wheelNeedsPassword = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = lib.mdDoc ''
        Whether members of `wheel` must enter a password for sudo. Defaults to
        `false` so the shared operator account works without embedding a shared
        password hash in the cluster-wide config.
      '';
    };

    lockRootPassword = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = lib.mdDoc ''
        Lock the local `root` password.
      '';
    };

    permitRootLogin = lib.mkOption {
      type = lib.types.enum [
        "yes"
        "prohibit-password"
        "forced-commands-only"
        "no"
      ];
      default = "prohibit-password";
      description = lib.mdDoc ''
        OpenSSH `PermitRootLogin` policy. Defaults to proxnix's current
        key-only `root` behavior; set to `no` for stricter legacy-bootstrap
        parity.
      '';
    };

    packages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = with pkgs; [
        nano
        git
        curl
        cacert
        lazyjournal
        pkgs.unstable.superfile
        lazydocker
        pkgs.unstable.snitch
        gdu
      ];
      description = lib.mdDoc ''
        Full convenience-package baseline installed on every proxnix-managed
        LXC. Override this when you want to replace the default set entirely.
      '';
    };

    extraPackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [];
      description = lib.mdDoc ''
        Extra convenience packages appended to `packages`. This is the
        recommended knob for a separate site/data repo when you only want to
        amend the shared baseline.
      '';
    };

    enableTimesyncd = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = lib.mdDoc ''
        Enable `systemd-timesyncd` on proxnix-managed LXCs.
      '';
    };

    manageJournald = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = lib.mdDoc ''
        Apply the shared journald retention and size limits.
      '';
    };

    journaldSystemMaxUse = lib.mkOption {
      type = lib.types.str;
      default = "200M";
      description = lib.mdDoc ''
        `SystemMaxUse` value for journald.
      '';
    };

    journaldRuntimeMaxUse = lib.mkOption {
      type = lib.types.str;
      default = "50M";
      description = lib.mdDoc ''
        `RuntimeMaxUse` value for journald.
      '';
    };

    journaldMaxRetentionSec = lib.mkOption {
      type = lib.types.str;
      default = "14day";
      description = lib.mdDoc ''
        `MaxRetentionSec` value for journald.
      '';
    };

    manageSwappiness = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = lib.mdDoc ''
        Manage `vm.swappiness` for proxnix-managed LXCs.
      '';
    };

    vmSwappiness = lib.mkOption {
      type = lib.types.int;
      default = 10;
      description = lib.mdDoc ''
        `vm.swappiness` value applied when `manageSwappiness` is enabled.
      '';
    };

  };

  config = lib.mkIf cfg.enable (lib.mkMerge [

    {
      environment.systemPackages = cfg.packages ++ cfg.extraPackages;

      security.sudo = {
        enable = true;
        wheelNeedsPassword = cfg.wheelNeedsPassword;
      };

      users.users.${cfg.adminUser} =
        {
          isNormalUser = true;
          uid = cfg.adminUid;
          description = "shared proxnix operator account";
          extraGroups = cfg.adminExtraGroups;
          shell = cfg.adminShell;
          openssh.authorizedKeys.keys = adminAuthorizedKeys;
        }
        // lib.optionalAttrs (cfg.adminPasswordHash != null) {
          hashedPassword = cfg.adminPasswordHash;
        }
        // lib.optionalAttrs (cfg.adminPasswordHash == null) {
          hashedPassword = "!";
        };

      services.openssh = {
        enable = lib.mkDefault true;
        settings = {
          PasswordAuthentication = false;
          KbdInteractiveAuthentication = false;
          ChallengeResponseAuthentication = false;
          PermitEmptyPasswords = false;
          PubkeyAuthentication = true;
          X11Forwarding = false;
          PermitRootLogin = cfg.permitRootLogin;
        };
      };
    }

    (lib.mkIf (cfg.adminPasswordHash == null && cfg.adminPasswordHashSecretName != null) {
      systemd.services.proxnix-common-admin-password = {
        description = "Apply shared proxnix admin password hash";
        wantedBy = [ "multi-user.target" ];
        after = [ "local-fs.target" ];
        unitConfig.ConditionPathExists = "/usr/local/bin/proxnix-secrets";
        serviceConfig.Type = "oneshot";
        script = ''
          set -euo pipefail

          temp_hash="$(mktemp /run/proxnix-admin-password-hash.XXXXXX)"
          trap 'rm -f "$temp_hash"' EXIT

          if ! /usr/local/bin/proxnix-secrets get ${lib.escapeShellArg cfg.adminPasswordHashSecretName} > "$temp_hash"; then
            echo "proxnix-common-admin-password: secret ${cfg.adminPasswordHashSecretName} not available yet, skipping" >&2
            exit 0
          fi

          hash="$(tr -d '\r\n' < "$temp_hash")"
          if [ -z "$hash" ]; then
            echo "proxnix-common-admin-password: decrypted hash is empty" >&2
            exit 1
          fi

          printf '%s:%s\n' ${lib.escapeShellArg cfg.adminUser} "$hash" | \
            ${pkgs.shadow}/bin/chpasswd -e
        '';
      };
    })

    (lib.mkIf cfg.lockRootPassword {
      users.users.root.hashedPassword = lib.mkDefault "!";
    })

    (lib.mkIf cfg.enableTimesyncd {
      services.timesyncd.enable = true;
    })

    (lib.mkIf cfg.manageJournald {
      services.journald.extraConfig = ''
        SystemMaxUse=${cfg.journaldSystemMaxUse}
        RuntimeMaxUse=${cfg.journaldRuntimeMaxUse}
        MaxRetentionSec=${cfg.journaldMaxRetentionSec}
      '';
    })

    (lib.mkIf cfg.manageSwappiness {
      boot.kernel.sysctl."vm.swappiness" = cfg.vmSwappiness;
    })

  ]);

}
