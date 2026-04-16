{ config, lib, pkgs, ... }:

let
  cfg = config.proxnix.common;
  secretsCfg = config.proxnix.secrets;
  inheritedRootAuthorizedKeys = config.users.users.root.openssh.authorizedKeys.keys or [];
  adminAuthorizedKeys =
    lib.unique (
      cfg.adminAuthorizedKeys
      ++ lib.optionals cfg.inheritRootAuthorizedKeys inheritedRootAuthorizedKeys
    );

  sanitizeUnitName = name:
    builtins.replaceStrings
      [ "/" " " ":" "@" "." ]
      [ "-" "-" "-" "-" "-" ]
      name;

  mkUnitListOption = description: lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [];
    description = lib.mdDoc description;
  };

  secretFetchCommand = secretCfg:
    "/usr/local/bin/proxnix-secrets ${secretCfg.lookup} ${lib.escapeShellArg secretCfg.secret}";

  secretSourceType = lib.types.submodule ({ name, ... }: {
    options = {
      secret = lib.mkOption {
        type = lib.types.str;
        default = name;
        description = lib.mdDoc ''
          proxnix secret name to read via `/usr/local/bin/proxnix-secrets`.
        '';
      };

      lookup = lib.mkOption {
        type = lib.types.enum [ "get" "get-shared" ];
        default = "get";
        description = lib.mdDoc ''
          proxnix-secrets lookup mode to use for this secret.
        '';
      };
    };
  });

  fileSecretType = lib.types.submodule ({ name, ... }: {
    options = {
      unit = lib.mkOption {
        type = lib.types.str;
        default = "proxnix-secret-file-${sanitizeUnitName name}";
        description = lib.mdDoc ''
          Generated systemd unit name. Use the same value across multiple
          entries to materialize them in one oneshot.
        '';
      };

      secret = lib.mkOption {
        type = lib.types.str;
        default = name;
        description = lib.mdDoc ''
          proxnix secret name to materialize.
        '';
      };

      lookup = lib.mkOption {
        type = lib.types.enum [ "get" "get-shared" ];
        default = "get";
        description = lib.mdDoc ''
          proxnix-secrets lookup mode to use for this secret.
        '';
      };

      path = lib.mkOption {
        type = lib.types.str;
        description = lib.mdDoc ''
          Absolute path where the plaintext runtime secret should be written.
        '';
      };

      owner = lib.mkOption {
        type = lib.types.str;
        default = "root";
        description = lib.mdDoc ''
          Owner for the materialized plaintext file.
        '';
      };

      group = lib.mkOption {
        type = lib.types.str;
        default = "root";
        description = lib.mdDoc ''
          Group for the materialized plaintext file.
        '';
      };

      mode = lib.mkOption {
        type = lib.types.str;
        default = "0400";
        description = lib.mdDoc ''
          File mode for the materialized plaintext file.
        '';
      };

      description = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = lib.mdDoc ''
          Optional systemd unit description override.
        '';
      };

      after = mkUnitListOption ''
        Extra `After=` dependencies for the generated materializer unit.
      '';

      before = mkUnitListOption ''
        Extra `Before=` dependencies for the generated materializer unit.
      '';

      wantedBy = mkUnitListOption ''
        Optional `WantedBy=` targets for the generated materializer unit.
      '';

      requiredBy = mkUnitListOption ''
        Optional `RequiredBy=` units for the generated materializer unit.
      '';

      partOf = mkUnitListOption ''
        Optional `PartOf=` units for the generated materializer unit.
      '';

      runtimeInputs = lib.mkOption {
        type = lib.types.listOf lib.types.package;
        default = [];
        description = lib.mdDoc ''
          Extra packages added to the materializer unit `PATH`.
        '';
      };
    };
  });

  oneshotSecretType = lib.types.submodule ({ name, ... }: {
    options = {
      unit = lib.mkOption {
        type = lib.types.str;
        default = "proxnix-secret-oneshot-${sanitizeUnitName name}";
        description = lib.mdDoc ''
          Generated systemd unit name. Use the same value across multiple
          entries to run them in one oneshot.
        '';
      };

      secret = lib.mkOption {
        type = lib.types.str;
        default = name;
        description = lib.mdDoc ''
          proxnix secret name to read before running the script.
        '';
      };

      lookup = lib.mkOption {
        type = lib.types.enum [ "get" "get-shared" ];
        default = "get";
        description = lib.mdDoc ''
          proxnix-secrets lookup mode to use for this secret.
        '';
      };

      script = lib.mkOption {
        type = lib.types.lines;
        description = lib.mdDoc ''
          Shell script run after the secret has been fetched to a temporary
          file. The plaintext path is exported as `PROXNIX_SECRET_FILE`.
        '';
      };

      optional = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = lib.mdDoc ''
          When enabled, a missing secret causes the generated unit to exit
          successfully without running the script.
        '';
      };

      description = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = lib.mdDoc ''
          Optional systemd unit description override.
        '';
      };

      after = mkUnitListOption ''
        Extra `After=` dependencies for the generated oneshot unit.
      '';

      before = mkUnitListOption ''
        Extra `Before=` dependencies for the generated oneshot unit.
      '';

      wantedBy = mkUnitListOption ''
        Optional `WantedBy=` targets for the generated oneshot unit.
      '';

      requiredBy = mkUnitListOption ''
        Optional `RequiredBy=` units for the generated oneshot unit.
      '';

      partOf = mkUnitListOption ''
        Optional `PartOf=` units for the generated oneshot unit.
      '';

      runtimeInputs = lib.mkOption {
        type = lib.types.listOf lib.types.package;
        default = [];
        description = lib.mdDoc ''
          Extra packages added to the oneshot unit `PATH`.
        '';
      };
    };
  });

  templateSecretType = lib.types.submodule ({ name, ... }: {
    options = {
      unit = lib.mkOption {
        type = lib.types.str;
        default = "proxnix-secret-template-${sanitizeUnitName name}";
        description = lib.mdDoc ''
          Generated systemd unit name. Use the same value across multiple
          entries to render them in one oneshot.
        '';
      };

      source = lib.mkOption {
        type = lib.types.path;
        description = lib.mdDoc ''
          Template source file. Each placeholder listed in `substitutions`
          will be replaced with the corresponding proxnix secret value.
        '';
      };

      destination = lib.mkOption {
        type = lib.types.str;
        description = lib.mdDoc ''
          Absolute destination path for the rendered file.
        '';
      };

      substitutions = lib.mkOption {
        type = lib.types.attrsOf secretSourceType;
        default = {};
        description = lib.mdDoc ''
          Mapping of placeholder text to proxnix secret lookups.
        '';
      };

      owner = lib.mkOption {
        type = lib.types.str;
        default = "root";
        description = lib.mdDoc ''
          Owner for the rendered file.
        '';
      };

      group = lib.mkOption {
        type = lib.types.str;
        default = "root";
        description = lib.mdDoc ''
          Group for the rendered file.
        '';
      };

      mode = lib.mkOption {
        type = lib.types.str;
        default = "0400";
        description = lib.mdDoc ''
          File mode for the rendered file.
        '';
      };

      createOnly = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = lib.mdDoc ''
          When enabled, skip rendering if the destination file already exists.
          Use this for mutable seed files that should only be initialized once.
        '';
      };

      description = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = lib.mdDoc ''
          Optional systemd unit description override.
        '';
      };

      after = mkUnitListOption ''
        Extra `After=` dependencies for the generated render unit.
      '';

      before = mkUnitListOption ''
        Extra `Before=` dependencies for the generated render unit.
      '';

      wantedBy = mkUnitListOption ''
        Optional `WantedBy=` targets for the generated render unit.
      '';

      requiredBy = mkUnitListOption ''
        Optional `RequiredBy=` units for the generated render unit.
      '';

      partOf = mkUnitListOption ''
        Optional `PartOf=` units for the generated render unit.
      '';

      runtimeInputs = lib.mkOption {
        type = lib.types.listOf lib.types.package;
        default = [];
        description = lib.mdDoc ''
          Extra packages added to the render unit `PATH`.
        '';
      };
    };
  });

  allSecretOps =
    (lib.mapAttrsToList (name: secretCfg: {
      kind = "file";
      name = name;
      unit = secretCfg.unit;
      cfg = secretCfg;
    }) secretsCfg.files)
    ++
    (lib.mapAttrsToList (name: templateCfg: {
      kind = "template";
      name = name;
      unit = templateCfg.unit;
      cfg = templateCfg;
    }) secretsCfg.templates)
    ++
    (lib.mapAttrsToList (name: secretCfg: {
      kind = "oneshot";
      name = name;
      unit = secretCfg.unit;
      cfg = secretCfg;
    }) secretsCfg.oneshot);

  secretOpsByUnit = lib.groupBy (op: op.unit) allSecretOps;

  pythonRenderer = lib.escapeShellArg (lib.concatStringsSep "\n" [
    "import os"
    "from pathlib import Path"
    ""
    "content = Path(os.environ[\"PROXNIX_TEMPLATE_SOURCE\"]).read_text()"
    "for idx in range(int(os.environ[\"PROXNIX_TEMPLATE_SECRET_COUNT\"])):"
    "    token = os.environ[f\"PROXNIX_TEMPLATE_SECRET_{idx}_TOKEN\"]"
    "    value = Path(os.environ[f\"PROXNIX_TEMPLATE_SECRET_{idx}_FILE\"]).read_text().rstrip(\"\\r\\n\")"
    "    content = content.replace(token, value)"
    "Path(os.environ[\"PROXNIX_TEMPLATE_OUTPUT\"]).write_text(content)"
  ]);

  mkFileOpScript = opId: secretCfg: ''
    dest=${lib.escapeShellArg secretCfg.path}
    mkdir -p "$(dirname "$dest")"
    tmp="$workdir/file-${sanitizeUnitName opId}.tmp"

    ${secretFetchCommand secretCfg} > "$tmp"
    chown ${lib.escapeShellArg secretCfg.owner}:${lib.escapeShellArg secretCfg.group} "$tmp"
    chmod ${lib.escapeShellArg secretCfg.mode} "$tmp"
    mv "$tmp" "$dest"
  '';

  mkOneshotOpScript = opId: secretCfg: ''
    secret_tmp="$workdir/oneshot-${sanitizeUnitName opId}.tmp"

    if ! ${secretFetchCommand secretCfg} > "$secret_tmp"; then
      if ${if secretCfg.optional then "true" else "false"}; then
        :
      else
        exit 1
      fi
    else
      export PROXNIX_SECRET_FILE="$secret_tmp"
      export SECRET_FILE="$secret_tmp"

      ${secretCfg.script}
    fi
  '';

  mkTemplateOpScript = opId: templateCfg:
    let
      placeholders = lib.attrNames templateCfg.substitutions;
      fetchLines = lib.concatStringsSep "\n" (lib.imap0 (idx: placeholder:
        let
          secretCfg = templateCfg.substitutions.${placeholder};
        in ''
          ${secretFetchCommand secretCfg} > "$template_workdir/secret-${toString idx}"
          export PROXNIX_TEMPLATE_SECRET_${toString idx}_TOKEN=${lib.escapeShellArg placeholder}
          export PROXNIX_TEMPLATE_SECRET_${toString idx}_FILE="$template_workdir/secret-${toString idx}"
        ''
      ) placeholders);
    in ''
      dest=${lib.escapeShellArg templateCfg.destination}
      if ${if templateCfg.createOnly then "[ -e \"$dest\" ]" else "false"}; then
        :
      else
        template_workdir="$workdir/template-${sanitizeUnitName opId}"
        mkdir -p "$template_workdir"

        ${fetchLines}

        export PROXNIX_TEMPLATE_SECRET_COUNT=${lib.escapeShellArg (toString (builtins.length placeholders))}
        export PROXNIX_TEMPLATE_SOURCE=${lib.escapeShellArg "${templateCfg.source}"}
        export PROXNIX_TEMPLATE_OUTPUT="$template_workdir/rendered"

        python3 -c ${pythonRenderer}

        dest_dir="$(dirname "$dest")"
        base_name="$(basename "$dest")"
        mkdir -p "$dest_dir"
        tmp="$template_workdir/.''${base_name}.tmp"
        cat "$template_workdir/rendered" > "$tmp"
        chown ${lib.escapeShellArg templateCfg.owner}:${lib.escapeShellArg templateCfg.group} "$tmp"
        chmod ${lib.escapeShellArg templateCfg.mode} "$tmp"
        mv "$tmp" "$dest"
      fi
    '';

  mkSecretUnitService = unit: ops:
    let
      descriptions = builtins.filter (d: d != null) (map (op: op.cfg.description) ops);
      kinds = map (op: op.kind) ops;
      desc =
        if descriptions != []
        then lib.head descriptions
        else if builtins.length ops == 1 && lib.elem "file" kinds
        then "Materialize proxnix secret ${(lib.head ops).cfg.secret}"
        else if builtins.length ops == 1 && lib.elem "template" kinds
        then "Render proxnix secret template ${(lib.head ops).name}"
        else if builtins.length ops == 1
        then "Run proxnix secret oneshot ${(lib.head ops).name}"
        else "Run proxnix secret materializer ${unit}";
      allAfter = lib.unique ([ "local-fs.target" ] ++ lib.concatMap (op: op.cfg.after) ops);
      allBefore = lib.unique (lib.concatMap (op: op.cfg.before) ops);
      allWantedBy = lib.unique (lib.concatMap (op: op.cfg.wantedBy) ops);
      allRequiredBy = lib.unique (lib.concatMap (op: op.cfg.requiredBy) ops);
      allPartOf = lib.unique (lib.concatMap (op: op.cfg.partOf) ops);
      allRuntimeInputs = lib.unique (lib.concatMap (op: op.cfg.runtimeInputs) ops);
      hasMaterializedState = lib.any (kind: kind != "oneshot") kinds;
      scriptBody = lib.concatStringsSep "\n\n" (map
        (op:
          if op.kind == "file" then
            mkFileOpScript "${unit}-${op.name}" op.cfg
          else if op.kind == "template" then
            mkTemplateOpScript "${unit}-${op.name}" op.cfg
          else
            mkOneshotOpScript "${unit}-${op.name}" op.cfg)
        ops);
    in
    lib.nameValuePair unit {
      description = desc;
      after = allAfter;
      before = allBefore;
      wantedBy = allWantedBy;
      requiredBy = allRequiredBy;
      partOf = allPartOf;
      unitConfig.ConditionPathExists = "/usr/local/bin/proxnix-secrets";
      serviceConfig = {
        Type = "oneshot";
        UMask = "0077";
        Environment = [ "HOME=/root" ];
      } // lib.optionalAttrs hasMaterializedState {
        RemainAfterExit = true;
      };
      path = [ pkgs.coreutils pkgs.python3Minimal ] ++ allRuntimeInputs;
      script = ''
        set -euo pipefail

        workdir="$(mktemp -d /run/proxnix-secret-unit-${sanitizeUnitName unit}.XXXXXX)"
        trap 'rm -rf "$workdir"' EXIT

        ${scriptBody}
      '';
    };
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

  options.proxnix.secrets = {
    files = lib.mkOption {
      type = lib.types.attrsOf fileSecretType;
      default = {};
      description = lib.mdDoc ''
        Declarative runtime secret files backed by proxnix-secrets.
      '';
    };

    templates = lib.mkOption {
      type = lib.types.attrsOf templateSecretType;
      default = {};
      description = lib.mdDoc ''
        Declarative template render units backed by proxnix-secrets.
      '';
    };

    oneshot = lib.mkOption {
      type = lib.types.attrsOf oneshotSecretType;
      default = {};
      description = lib.mdDoc ''
        Declarative oneshot secret consumers backed by proxnix-secrets.
      '';
    };
  };

  config = lib.mkIf cfg.enable (lib.mkMerge [

    {
      environment.systemPackages = cfg.packages ++ cfg.extraPackages;

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
    }

    (lib.mkIf (cfg.adminPasswordHash == null && cfg.adminPasswordHashSecretName != null) {
      proxnix.secrets.oneshot.proxnix-common-admin-password = {
        description = "Apply shared proxnix admin password hash";
        secret = cfg.adminPasswordHashSecretName;
        lookup = "get-shared";
        optional = true;
        wantedBy = [ "multi-user.target" ];
        runtimeInputs = [ pkgs.shadow ];
        script = ''
          hash="$(tr -d '\r\n' < "$PROXNIX_SECRET_FILE")"
          if [ -z "$hash" ]; then
            echo "proxnix-common-admin-password: decrypted hash is empty" >&2
            exit 1
          fi

          printf '%s:%s\n' ${lib.escapeShellArg cfg.adminUser} "$hash" | \
            chpasswd -e
        '';
      };
    })

    {
      systemd.services =
        lib.mapAttrs' mkSecretUnitService secretOpsByUnit;
    }

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
