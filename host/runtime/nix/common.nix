{ config, lib, pkgs, ... }:

let
  cfg = config.proxnix.common;
  secretsCfg = config.proxnix._internal.secrets;
  activationFiles =
    lib.filterAttrs (_: secretCfg: secretCfg.lifecycle == "activation") secretsCfg.files;
  serviceFiles =
    lib.filterAttrs (_: secretCfg: secretCfg.lifecycle == "service") secretsCfg.files;
  createOnlyTemplates =
    lib.filterAttrs (_: templateCfg: templateCfg.createOnly) secretsCfg.templates;
  managedTemplates =
    lib.filterAttrs (_: templateCfg: !templateCfg.createOnly) secretsCfg.templates;
  activationTemplates =
    lib.filterAttrs (_: templateCfg: templateCfg.lifecycle == "activation") managedTemplates;
  serviceTemplates =
    lib.filterAttrs (_: templateCfg: templateCfg.lifecycle == "service") managedTemplates;
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

  activationFileUnitName = name: "proxnix-secret-file-${sanitizeUnitName name}";
  activationTemplateUnitName = name: "proxnix-secret-template-${sanitizeUnitName name}";
  systemdServiceAttrName = service:
    if lib.hasSuffix ".service" service then lib.removeSuffix ".service" service else service;
  systemdUnitName = service:
    if lib.hasSuffix ".service" service then service else "${service}.service";

  mkUnitListOption = description: lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [];
    description = lib.mdDoc description;
  };

  secretFetchCommand = secretCfg:
    "/var/lib/proxnix/runtime/bin/proxnix-secrets get ${lib.escapeShellArg secretCfg.secret}";

  secretSourceType = lib.types.submodule ({ name, ... }: {
    options = {
      secret = lib.mkOption {
        type = lib.types.str;
        default = name;
        description = lib.mdDoc ''
          proxnix secret name to read via `/var/lib/proxnix/runtime/bin/proxnix-secrets`.
        '';
      };

    };
  });

  fileSecretType = lib.types.submodule ({ name, ... }: {
    options = {
      secret = lib.mkOption {
        type = lib.types.str;
        default = name;
        description = lib.mdDoc ''
          proxnix secret name to materialize.
        '';
      };

      lifecycle = lib.mkOption {
        type = lib.types.enum [ "activation" "service" ];
        default = "activation";
        description = lib.mdDoc ''
          Secret lifecycle policy.
        '';
      };

      service = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = lib.mdDoc ''
          Owning systemd service when `lifecycle = "service"`.
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

      restartUnits = mkUnitListOption ''
        Optional systemd units restarted when this secret changes.
      '';

      reloadUnits = mkUnitListOption ''
        Optional systemd units reloaded when this secret changes.
      '';
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

      literalSubstitutions = lib.mkOption {
        type = lib.types.attrsOf lib.types.str;
        default = {};
        description = lib.mdDoc ''
          Mapping of placeholder text to literal values.
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

      lifecycle = lib.mkOption {
        type = lib.types.enum [ "activation" "service" ];
        default = "activation";
        description = lib.mdDoc ''
          Template lifecycle policy.
        '';
      };

      service = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = lib.mdDoc ''
          Owning systemd service when `lifecycle = "service"`.
        '';
      };

      restartUnits = mkUnitListOption ''
        Optional systemd units restarted when this rendered template changes.
      '';

      reloadUnits = mkUnitListOption ''
        Optional systemd units reloaded when this rendered template changes.
      '';

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

  configValueType = lib.types.oneOf [
    lib.types.bool
    lib.types.int
    lib.types.float
    lib.types.str
    lib.types.path
  ];
  renderConfigValue = value:
    if builtins.isBool value then
      if value then "true" else "false"
    else
      toString value;

  mkPublicSecretSourceType = secretName: lib.types.submodule {
    options = {
      scope = lib.mkOption {
        type = lib.types.enum [
          "container"
          "group"
          "shared"
        ];
        default = "container";
        description = lib.mdDoc ''
          Secret lookup scope in the proxnix authoring model.
        '';
      };

      group = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = lib.mdDoc ''
          Secret group name when `scope = "group"`.
        '';
      };

      name = lib.mkOption {
        type = lib.types.str;
        default = secretName;
        description = lib.mdDoc ''
          proxnix secret-store key to fetch at runtime.
        '';
      };
    };
  };

  mkPublicSecretFileType = secretName: lib.types.submodule ({ ... }: {
    options = {
      lifecycle = lib.mkOption {
        type = lib.types.enum [ "container" ];
        default = "container";
        description = lib.mdDoc ''
          File lifecycle policy for this secret.
        '';
      };

      owner = lib.mkOption {
        type = lib.types.str;
        default = "root";
        description = lib.mdDoc ''
          Owner for the materialized secret file.
        '';
      };

      group = lib.mkOption {
        type = lib.types.str;
        default = "root";
        description = lib.mdDoc ''
          Group for the materialized secret file.
        '';
      };

      mode = lib.mkOption {
        type = lib.types.str;
        default = "0400";
        description = lib.mdDoc ''
          File mode for the materialized secret file.
        '';
      };

      restartUnits = mkUnitListOption ''
        Optional systemd units restarted when this secret file is updated.
      '';

      reloadUnits = mkUnitListOption ''
        Optional systemd units reloaded when this secret file is updated.
      '';

      path = lib.mkOption {
        type = lib.types.str;
        readOnly = true;
        description = lib.mdDoc ''
          Materialized secret file path.
        '';
      };
    };

    config.path = "/var/lib/proxnix/secrets/${sanitizeUnitName secretName}";
  });

  mkPublicSecretCredentialType = secretName: lib.types.submodule {
    options = {
      service = lib.mkOption {
        type = lib.types.str;
        description = lib.mdDoc ''
          Owning systemd service for this credential binding.
        '';
      };

      name = lib.mkOption {
        type = lib.types.str;
        default = secretName;
        description = lib.mdDoc ''
          Credential identifier exposed to the service.
        '';
      };
    };
  };

  publicSecretEnvType = lib.types.submodule {
    options = {
      service = lib.mkOption {
        type = lib.types.str;
        description = lib.mdDoc ''
          Owning systemd service for this environment binding.
        '';
      };

      variable = lib.mkOption {
        type = lib.types.str;
        description = lib.mdDoc ''
          Environment variable name exposed to the service.
        '';
      };
    };
  };

  publicSecretType = lib.types.submodule ({ name, ... }: {
    options = {
      source = lib.mkOption {
        type = mkPublicSecretSourceType name;
        default = {};
        description = lib.mdDoc ''
          Secret source declaration.
        '';
      };

      file = lib.mkOption {
        type = lib.types.nullOr (mkPublicSecretFileType name);
        default = null;
        description = lib.mdDoc ''
          Container-lifetime file delivery for this secret.
        '';
      };

      credential = lib.mkOption {
        type = lib.types.nullOr (mkPublicSecretCredentialType name);
        default = null;
        description = lib.mdDoc ''
          Native systemd credential delivery for this secret.
        '';
      };

      env = lib.mkOption {
        type = lib.types.nullOr publicSecretEnvType;
        default = null;
        description = lib.mdDoc ''
          Environment-file delivery for this secret.
        '';
      };
    };
  });

  publicConfigType = lib.types.submodule ({ name, ... }: {
    options = {
      source = lib.mkOption {
        type = lib.types.str;
        default = name;
        description = lib.mdDoc ''
          Logical template source name. Defaults to the config attr name.
        '';
      };

      service = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = lib.mdDoc ''
          Optional owning systemd service for ordering and restart wiring.
        '';
      };

      createOnly = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = lib.mdDoc ''
          Seed this config once and leave an existing file untouched on later
          boots.
        '';
      };

      secretValues = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [];
        description = lib.mdDoc ''
          Public secret names available to this template as
          `{{ secrets.<name> }}`.
        '';
      };

      values = lib.mkOption {
        type = lib.types.attrsOf configValueType;
        default = {};
        description = lib.mdDoc ''
          Literal values available to this template as `{{ values.<name> }}`.
        '';
      };

      owner = lib.mkOption {
        type = lib.types.str;
        default = "root";
        description = lib.mdDoc ''
          Owner for the rendered config file.
        '';
      };

      group = lib.mkOption {
        type = lib.types.str;
        default = "root";
        description = lib.mdDoc ''
          Group for the rendered config file.
        '';
      };

      mode = lib.mkOption {
        type = lib.types.str;
        default = "0400";
        description = lib.mdDoc ''
          File mode for the rendered config file.
        '';
      };

      restartUnits = mkUnitListOption ''
        Optional systemd units restarted when this config is updated.
      '';

      reloadUnits = mkUnitListOption ''
        Optional systemd units reloaded when this config is updated.
      '';

      path = lib.mkOption {
        type = lib.types.str;
        default = "/var/lib/proxnix/configs/${sanitizeUnitName name}";
        description = lib.mdDoc ''
          Materialized config path.
        '';
      };
    };
  });

  lowLevelSecretsOptions = {
    files = lib.mkOption {
      type = lib.types.attrsOf fileSecretType;
      default = {};
      description = lib.mdDoc ''
        Internal declarative runtime secret files backed by proxnix-secrets.
      '';
    };

    templates = lib.mkOption {
      type = lib.types.attrsOf templateSecretType;
      default = {};
      description = lib.mdDoc ''
        Internal declarative template render units backed by proxnix-secrets.
      '';
    };

    oneshot = lib.mkOption {
      type = lib.types.attrsOf oneshotSecretType;
      default = {};
      description = lib.mdDoc ''
        Internal declarative oneshot secret consumers backed by proxnix-secrets.
      '';
    };
  };

  publicSecrets = config.proxnix.secrets;
  publicConfigs = config.proxnix.configs;
  configTemplateSources = config.proxnix._internal.configTemplateSources;

  lookupPublicSecretStoreName = secretName:
    if builtins.hasAttr secretName publicSecrets then
      publicSecrets.${secretName}.source.name
    else
      secretName;

  lookupConfigTemplateSource = configName: configCfg:
    if builtins.hasAttr configCfg.source configTemplateSources then
      configTemplateSources.${configCfg.source}
    else
      pkgs.writeText "missing-proxnix-config-template-${sanitizeUnitName configName}" "";

  publicCompatFileSecrets =
    lib.mapAttrs' (secretName: secretCfg:
      lib.nameValuePair "public-secret-file-${sanitizeUnitName secretName}" {
        secret = secretCfg.source.name;
        path = secretCfg.file.path;
        owner = secretCfg.file.owner;
        group = secretCfg.file.group;
        mode = secretCfg.file.mode;
        restartUnits = secretCfg.file.restartUnits;
        reloadUnits = secretCfg.file.reloadUnits;
      }
    ) (lib.filterAttrs (_: secretCfg: secretCfg.file != null) publicSecrets);

  mkConfigSecretToken = secretName: "{{ secrets.${secretName} }}";
  mkConfigValueToken = valueName: "{{ values.${valueName} }}";

  publicCompatTemplates =
    lib.mapAttrs' (configName: configCfg:
      let
        serviceUnit =
          if configCfg.service == null then null else systemdUnitName configCfg.service;
        serviceRestartUnits = lib.optionals (serviceUnit != null && !configCfg.createOnly) [ serviceUnit ];
        createOnlyBefore = lib.optionals (serviceUnit != null && configCfg.createOnly) [ serviceUnit ];
        createOnlyRequiredBy = createOnlyBefore;
        createOnlyWantedBy = lib.optionals (serviceUnit == null && configCfg.createOnly) [ "multi-user.target" ];
      in
      lib.nameValuePair "public-config-${sanitizeUnitName configName}" {
        source = lookupConfigTemplateSource configName configCfg;
        destination = configCfg.path;
        substitutions = lib.listToAttrs (map
          (secretName: {
            name = mkConfigSecretToken secretName;
            value = {
              secret = lookupPublicSecretStoreName secretName;
            };
          })
          configCfg.secretValues);
        literalSubstitutions = lib.mapAttrs' (valueName: value:
          lib.nameValuePair (mkConfigValueToken valueName) (renderConfigValue value)
        ) configCfg.values;
        owner = configCfg.owner;
        group = configCfg.group;
        mode = configCfg.mode;
        createOnly = configCfg.createOnly;
        restartUnits = configCfg.restartUnits ++ serviceRestartUnits;
        reloadUnits = configCfg.reloadUnits;
        before = createOnlyBefore;
        requiredBy = createOnlyRequiredBy;
        wantedBy = createOnlyWantedBy;
      }
    ) publicConfigs;

  publicRuntimeSecretOps =
    lib.concatLists (lib.mapAttrsToList (secretName: secretCfg:
      let
        credentialPath =
          if secretCfg.credential == null then
            null
          else
            "/run/proxnix/credentials/${sanitizeUnitName (systemdServiceAttrName secretCfg.credential.service)}/${sanitizeUnitName secretCfg.credential.name}";
        envPath =
          if secretCfg.env == null then
            null
          else
            "/run/proxnix/environment/${sanitizeUnitName (systemdServiceAttrName secretCfg.env.service)}/${sanitizeUnitName secretName}.env";
      in
      lib.optionals (secretCfg.credential != null) [
        {
          kind = "credential";
          secretName = secretName;
          service = systemdServiceAttrName secretCfg.credential.service;
          serviceUnit = systemdUnitName secretCfg.credential.service;
          bindingName = secretCfg.credential.name;
          path = credentialPath;
          cfg = {
            secret = secretCfg.source.name;
            path = credentialPath;
            owner = "root";
            group = "root";
            mode = "0400";
          };
        }
      ]
      ++
      lib.optionals (secretCfg.env != null) [
        {
          kind = "env";
          secretName = secretName;
          service = systemdServiceAttrName secretCfg.env.service;
          serviceUnit = systemdUnitName secretCfg.env.service;
          bindingName = secretCfg.env.variable;
          path = envPath;
          cfg = {
            source = pkgs.writeText "proxnix-env-${sanitizeUnitName secretName}" ''
              ${secretCfg.env.variable}=__PROXNIX_ENV_VALUE__
            '';
            destination = envPath;
            substitutions = {
              "__PROXNIX_ENV_VALUE__" = {
                secret = secretCfg.source.name;
              };
            };
            literalSubstitutions = {};
            owner = "root";
            group = "root";
            mode = "0400";
            createOnly = false;
          };
        }
      ]
    ) publicSecrets);

  publicRuntimeSecretOpsByService = lib.groupBy (op: op.service) publicRuntimeSecretOps;

  publicRuntimeSecretUnitName = service: "proxnix-public-runtime-secret-${sanitizeUnitName service}";

  mkPublicRuntimeSecretUnitService = service: ops:
    let
      unit = publicRuntimeSecretUnitName service;
      serviceUnit = (lib.head ops).serviceUnit;
      scriptBody = lib.concatStringsSep "\n\n" (map
        (op:
          if op.kind == "credential" then
            mkFileOpScript "${unit}-${op.secretName}" op.cfg
          else
            mkTemplateOpScript "${unit}-${op.secretName}" op.cfg)
        ops);
    in
    lib.nameValuePair unit {
      description = "Prepare proxnix runtime secrets for ${serviceUnit}";
      before = [ serviceUnit ];
      partOf = [ serviceUnit ];
      unitConfig.ConditionPathExists = "/var/lib/proxnix/runtime/bin/proxnix-secrets";
      serviceConfig = {
        Type = "oneshot";
        UMask = "0077";
        Environment = [ "HOME=/root" ];
      };
      path = [ pkgs.coreutils ];
      script = ''
        set -euo pipefail

        workdir="$(mktemp -d /run/proxnix-runtime-secret-${sanitizeUnitName unit}.XXXXXX)"
        trap 'rm -rf "$workdir"' EXIT

        ${scriptBody}
      '';
    };

  publicRuntimeSecretUnitConfigs =
    lib.mapAttrs' mkPublicRuntimeSecretUnitService publicRuntimeSecretOpsByService;

  publicRuntimeSecretServiceConfigs =
    lib.mapAttrs' (service: ops:
      let
        unitName = "${publicRuntimeSecretUnitName service}.service";
        credentialBindings = map (op: "${op.bindingName}:${op.path}") (
          builtins.filter (op: op.kind == "credential") ops
        );
        envBindings = map (op: op.path) (
          builtins.filter (op: op.kind == "env") ops
        );
      in
      lib.nameValuePair service ({
        wants = lib.mkAfter [ unitName ];
        after = lib.mkAfter [ unitName ];
      } // lib.optionalAttrs (credentialBindings != [] || envBindings != []) {
        serviceConfig =
          (lib.optionalAttrs (credentialBindings != []) {
            LoadCredential = lib.mkAfter credentialBindings;
          })
          // (lib.optionalAttrs (envBindings != []) {
            EnvironmentFile = lib.mkAfter envBindings;
          });
      })
    ) publicRuntimeSecretOpsByService;

  duplicateBindingAssertions = label: bindings:
    lib.mapAttrsToList (_: dupBindings: {
      assertion = false;
      message = ''
        ${label} ${lib.head dupBindings} is declared more than once.
      '';
    }) (lib.filterAttrs (_: dupBindings: builtins.length dupBindings > 1) (
      lib.groupBy (binding: binding) bindings
    ));

  publicModelAssertions =
    lib.flatten [
      (lib.mapAttrsToList (secretName: secretCfg: [
        {
          assertion = secretCfg.source.scope != "group" || secretCfg.source.group != null;
          message = ''
            proxnix.secrets.${secretName}.source.group is required when
            `scope = "group"`.
          '';
        }
        {
          assertion = secretCfg.source.scope == "group" || secretCfg.source.group == null;
          message = ''
            proxnix.secrets.${secretName}.source.group is only allowed when
            `scope = "group"`.
          '';
        }
      ]) publicSecrets)

      (lib.mapAttrsToList (configName: configCfg: [
        {
          assertion = builtins.hasAttr configCfg.source configTemplateSources;
          message = ''
            proxnix.configs.${configName}.source refers to an unknown logical
            template `${configCfg.source}`. Define it under
            `proxnix._internal.configTemplateSources`.
          '';
        }
        {
          assertion = lib.all (secretName: builtins.hasAttr secretName publicSecrets) configCfg.secretValues;
          message = ''
            proxnix.configs.${configName}.secretValues must only reference
            secrets declared under `proxnix.secrets`.
          '';
        }
        {
          assertion = !configCfg.createOnly || configCfg.restartUnits == [];
          message = ''
            proxnix.configs.${configName}.restartUnits is not supported for
            createOnly configs.
          '';
        }
        {
          assertion = !configCfg.createOnly || configCfg.reloadUnits == [];
          message = ''
            proxnix.configs.${configName}.reloadUnits is not supported for
            createOnly configs.
          '';
        }
      ]) publicConfigs)

      (duplicateBindingAssertions
        "Duplicate proxnix credential binding"
        (map (op: "${op.service}:${op.bindingName}") (builtins.filter (op: op.kind == "credential") publicRuntimeSecretOps)))

      (duplicateBindingAssertions
        "Duplicate proxnix environment binding"
        (map (op: "${op.service}:${op.bindingName}") (builtins.filter (op: op.kind == "env") publicRuntimeSecretOps)))
    ];

  commonOptions = {
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

  allSecretOps =
    (lib.mapAttrsToList (name: templateCfg: {
      kind = "template";
      name = name;
      unit = templateCfg.unit;
      cfg = templateCfg;
    }) createOnlyTemplates)
    ++
    (lib.mapAttrsToList (name: secretCfg: {
      kind = "oneshot";
      name = name;
      unit = secretCfg.unit;
      cfg = secretCfg;
    }) secretsCfg.oneshot);

  secretOpsByUnit = lib.groupBy (op: op.unit) allSecretOps;

  activationFileServiceConfigs =
    lib.mapAttrs' (name: secretCfg:
      lib.nameValuePair (activationFileUnitName name) {
        description = "Materialize proxnix secret ${secretCfg.secret}";
        after = [ "local-fs.target" ];
        before = lib.unique (secretCfg.restartUnits ++ secretCfg.reloadUnits);
        wantedBy = lib.unique ([ "multi-user.target" ] ++ secretCfg.restartUnits ++ secretCfg.reloadUnits);
        unitConfig.ConditionPathExists = "/var/lib/proxnix/runtime/bin/proxnix-secrets";
        serviceConfig = {
          Type = "oneshot";
          UMask = "0077";
          Environment = [ "HOME=/root" ];
          RemainAfterExit = true;
        };
        path = [ pkgs.coreutils ];
        script = ''
          set -euo pipefail

          workdir="$(mktemp -d /run/proxnix-secret-unit-${sanitizeUnitName (activationFileUnitName name)}.XXXXXX)"
          trap 'rm -rf "$workdir"' EXIT

          ${mkFileOpScript "${activationFileUnitName name}-${name}" secretCfg}
        '';
      }
    ) activationFiles;

  activationTemplateServiceConfigs =
    lib.mapAttrs' (name: templateCfg:
      lib.nameValuePair (activationTemplateUnitName name) {
        description = "Render proxnix secret template ${name}";
        after = [ "local-fs.target" ];
        before = lib.unique (templateCfg.restartUnits ++ templateCfg.reloadUnits);
        wantedBy = lib.unique ([ "multi-user.target" ] ++ templateCfg.restartUnits ++ templateCfg.reloadUnits);
        unitConfig.ConditionPathExists = "/var/lib/proxnix/runtime/bin/proxnix-secrets";
        serviceConfig = {
          Type = "oneshot";
          UMask = "0077";
          Environment = [ "HOME=/root" ];
          RemainAfterExit = true;
        };
        path = [ pkgs.coreutils ];
        script = ''
          set -euo pipefail

          workdir="$(mktemp -d /run/proxnix-secret-unit-${sanitizeUnitName (activationTemplateUnitName name)}.XXXXXX)"
          trap 'rm -rf "$workdir"' EXIT

          ${mkTemplateOpScript "${activationTemplateUnitName name}-${name}" templateCfg}
        '';
      }
    ) activationTemplates;

  serviceFileOpScript = opId: secretCfg: ''
    dest=${lib.escapeShellArg secretCfg.path}
    mkdir -p "$(dirname "$dest")"
    tmp="$workdir/file-${sanitizeUnitName opId}.tmp"

    ${secretFetchCommand secretCfg} > "$tmp"
    chown ${lib.escapeShellArg secretCfg.owner}:${lib.escapeShellArg secretCfg.group} "$tmp"
    chmod ${lib.escapeShellArg secretCfg.mode} "$tmp"
    mv "$tmp" "$dest"
  '';

  serviceTemplateOpScript = opId: templateCfg:
    let
      secretPlaceholders = lib.attrNames templateCfg.substitutions;
      literalPlaceholders = lib.attrNames templateCfg.literalSubstitutions;
      gomplateExpression = gomplateRenderExpression secretPlaceholders literalPlaceholders;
      fetchLines = lib.concatStringsSep "\n" (lib.imap0 (idx: placeholder:
        let
          secretCfg = templateCfg.substitutions.${placeholder};
        in ''
          ${secretFetchCommand secretCfg} > "$template_workdir/secret-${toString idx}"
        ''
      ) secretPlaceholders);
      literalLines = lib.concatStringsSep "\n" (lib.imap0 (idx: placeholder: ''
        printf '%s' ${lib.escapeShellArg templateCfg.literalSubstitutions.${placeholder}} > "$template_workdir/literal-${toString idx}"
      '') literalPlaceholders);
    in ''
      dest=${lib.escapeShellArg templateCfg.destination}
      template_workdir="$workdir/template-${sanitizeUnitName opId}"
      mkdir -p "$template_workdir"

      ${fetchLines}
      ${literalLines}
      ${gomplateDatasourceArgs "${templateCfg.source}" secretPlaceholders literalPlaceholders}

      ${pkgs.gomplate}/bin/gomplate "''${datasource_args[@]}" -i ${gomplateExpression} > "$template_workdir/rendered"

      mkdir -p "$(dirname "$dest")"
      tmp="$template_workdir/output"
      cat "$template_workdir/rendered" > "$tmp"
      chown ${lib.escapeShellArg templateCfg.owner}:${lib.escapeShellArg templateCfg.group} "$tmp"
      chmod ${lib.escapeShellArg templateCfg.mode} "$tmp"
      mv "$tmp" "$dest"
    '';

  serviceSecretOps =
    (lib.mapAttrsToList (name: secretCfg: {
      kind = "file";
      name = name;
      service = secretCfg.service;
      cfg = secretCfg;
      cleanupPath = secretCfg.path;
    }) serviceFiles)
    ++
    (lib.mapAttrsToList (name: templateCfg: {
      kind = "template";
      name = name;
      service = templateCfg.service;
      cfg = templateCfg;
      cleanupPath = templateCfg.destination;
    }) serviceTemplates);

  serviceSecretOpsByService =
    lib.filterAttrs (service: _: service != null)
      (lib.groupBy (op: systemdServiceAttrName op.service) serviceSecretOps);

  mkServiceSecretPreStart = service: ops:
    let
      scriptBody = lib.concatStringsSep "\n\n" (map
        (op:
          if op.kind == "file" then
            serviceFileOpScript "${service}-${op.name}" op.cfg
          else
            serviceTemplateOpScript "${service}-${op.name}" op.cfg)
        ops);
    in ''
      set -euo pipefail

      workdir="$(mktemp -d /run/proxnix-service-secret-${sanitizeUnitName service}.XXXXXX)"
      trap 'rm -rf "$workdir"' EXIT

      ${scriptBody}
    '';

  mkServiceSecretPostStop = ops:
    let
      cleanupLines = lib.concatStringsSep "\n" (map (op: ''
        rm -f ${lib.escapeShellArg op.cleanupPath}
      '') ops);
    in ''
      set -euo pipefail
      ${cleanupLines}
    '';

  serviceSecretServiceConfigs =
    lib.mapAttrs' (service: ops:
      lib.nameValuePair service {
        preStart = lib.mkAfter (mkServiceSecretPreStart service ops);
        postStop = lib.mkAfter (mkServiceSecretPostStop ops);
      path = [ pkgs.coreutils ];
      }
    ) serviceSecretOpsByService;

  invalidManagedTemplateAssertions =
    lib.flatten (lib.mapAttrsToList (name: templateCfg:
      let
        usesLegacyUnitKnobs =
          templateCfg.unit != "proxnix-secret-template-${sanitizeUnitName name}"
          || templateCfg.description != null
          || templateCfg.after != []
          || templateCfg.before != []
          || templateCfg.wantedBy != []
          || templateCfg.requiredBy != []
          || templateCfg.partOf != []
          || templateCfg.runtimeInputs != [];
      in
      lib.optionals (!templateCfg.createOnly) [
        {
          assertion = !usesLegacyUnitKnobs;
          message = ''
            proxnix._internal.secrets.templates.${name}: unit, description, after, before,
            wantedBy, requiredBy, partOf, and runtimeInputs are only supported
            for createOnly templates.
          '';
        }
      ]
    ) secretsCfg.templates);

  invalidServiceFileAssertions =
    lib.flatten (lib.mapAttrsToList (name: secretCfg:
      lib.optionals (secretCfg.lifecycle == "service") [
        {
          assertion = secretCfg.service != null;
          message = ''
            proxnix._internal.secrets.files.${name}: service must be set when
            lifecycle = "service".
          '';
        }
        {
          assertion =
            lib.hasPrefix "/run/" secretCfg.path
            || secretCfg.path == "/run"
            || lib.hasPrefix "/var/run/" secretCfg.path
            || secretCfg.path == "/var/run";
          message = ''
            proxnix._internal.secrets.files.${name}: service-lifetime secrets must live
            under /run or /var/run so proxnix can clean them up safely.
          '';
        }
        {
          assertion = secretCfg.restartUnits == [];
          message = ''
            proxnix._internal.secrets.files.${name}: restartUnits is only supported for
            activation-lifetime secrets.
          '';
        }
        {
          assertion = secretCfg.reloadUnits == [];
          message = ''
            proxnix._internal.secrets.files.${name}: reloadUnits is only supported for
            activation-lifetime secrets.
          '';
        }
      ]
    ) secretsCfg.files);

  invalidActivationFileAssertions =
    lib.flatten (lib.mapAttrsToList (name: secretCfg:
      lib.optionals (secretCfg.lifecycle == "activation") [
        {
          assertion =
            !(lib.hasPrefix "/run/" secretCfg.path
            || secretCfg.path == "/run"
            || lib.hasPrefix "/var/run/" secretCfg.path
            || secretCfg.path == "/var/run");
          message = ''
            proxnix._internal.secrets.files.${name}: activation-lifetime secrets must not
            live under /run or /var/run because they need to survive container
            restarts.
          '';
        }
        {
          assertion = secretCfg.service == null;
          message = ''
            proxnix._internal.secrets.files.${name}: service is only supported when
            lifecycle = "service".
          '';
        }
      ]
    ) secretsCfg.files);

  invalidServiceTemplateAssertions =
    lib.flatten (lib.mapAttrsToList (name: templateCfg:
      lib.optionals (templateCfg.lifecycle == "service") [
        {
          assertion = !templateCfg.createOnly;
          message = ''
            proxnix._internal.secrets.templates.${name}: createOnly templates cannot use
            lifecycle = "service".
          '';
        }
        {
          assertion = templateCfg.service != null;
          message = ''
            proxnix._internal.secrets.templates.${name}: service must be set when
            lifecycle = "service".
          '';
        }
        {
          assertion =
            lib.hasPrefix "/run/" templateCfg.destination
            || templateCfg.destination == "/run"
            || lib.hasPrefix "/var/run/" templateCfg.destination
            || templateCfg.destination == "/var/run";
          message = ''
            proxnix._internal.secrets.templates.${name}: service-lifetime templates must
            live under /run or /var/run so proxnix can clean them up safely.
          '';
        }
        {
          assertion = templateCfg.restartUnits == [];
          message = ''
            proxnix._internal.secrets.templates.${name}: restartUnits is only supported
            for activation-lifetime templates.
          '';
        }
        {
          assertion = templateCfg.reloadUnits == [];
          message = ''
            proxnix._internal.secrets.templates.${name}: reloadUnits is only supported
            for activation-lifetime templates.
          '';
        }
      ]
    ) secretsCfg.templates);

  invalidActivationTemplateAssertions =
    lib.flatten (lib.mapAttrsToList (name: templateCfg:
      lib.optionals (templateCfg.lifecycle == "activation" && !templateCfg.createOnly) [
        {
          assertion =
            !(lib.hasPrefix "/run/" templateCfg.destination
            || templateCfg.destination == "/run"
            || lib.hasPrefix "/var/run/" templateCfg.destination
            || templateCfg.destination == "/var/run");
          message = ''
            proxnix._internal.secrets.templates.${name}: activation-lifetime templates
            must not live under /run or /var/run because they need to survive
            container restarts.
          '';
        }
        {
          assertion = templateCfg.service == null;
          message = ''
            proxnix._internal.secrets.templates.${name}: service is only supported when
            lifecycle = "service".
          '';
        }
      ]
    ) secretsCfg.templates);

  invalidCreateOnlyTemplateAssertions =
    lib.flatten (lib.mapAttrsToList (name: templateCfg:
      lib.optionals templateCfg.createOnly [
        {
          assertion = templateCfg.restartUnits == [];
          message = ''
            proxnix._internal.secrets.templates.${name}: restartUnits is only supported
            for managed templates, not createOnly seed templates.
          '';
        }
        {
          assertion = templateCfg.reloadUnits == [];
          message = ''
            proxnix._internal.secrets.templates.${name}: reloadUnits is only supported
            for managed templates, not createOnly seed templates.
          '';
        }
      ]
    ) secretsCfg.templates);

  gomplateRenderExpression = secretPlaceholders: literalPlaceholders:
    let
      sourceExpr = ''include ${builtins.toJSON "template_source"}'';
      secretTransforms = lib.imap0 (idx: placeholder:
        '' | strings.ReplaceAll ${builtins.toJSON placeholder} (include ${builtins.toJSON "secret_${toString idx}"} | strings.TrimRight ${builtins.toJSON "\r\n"})''
      ) secretPlaceholders;
      literalTransforms = lib.imap0 (idx: placeholder:
        '' | strings.ReplaceAll ${builtins.toJSON placeholder} (include ${builtins.toJSON "literal_${toString idx}"})''
      ) literalPlaceholders;
    in
    lib.escapeShellArg "{{ ${sourceExpr}${lib.concatStrings secretTransforms}${lib.concatStrings literalTransforms} }}";

  gomplateDatasourceArgs = templateSource: secretPlaceholders: literalPlaceholders: ''
    datasource_args=(
      --datasource ${lib.escapeShellArg "template_source=file://${templateSource}?type=text/plain"}
    )
    ${lib.concatStringsSep "\n" (lib.imap0 (idx: _: ''
      datasource_args+=(--datasource "secret_${toString idx}=file://$template_workdir/secret-${toString idx}?type=text/plain")
    '') secretPlaceholders)}
    ${lib.concatStringsSep "\n" (lib.imap0 (idx: _: ''
      datasource_args+=(--datasource "literal_${toString idx}=file://$template_workdir/literal-${toString idx}?type=text/plain")
    '') literalPlaceholders)}
  '';

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
    elif [ ! -s "$secret_tmp" ]; then
      if ${if secretCfg.optional then "true" else "false"}; then
        :
      else
        echo "proxnix secret ${lib.escapeShellArg secretCfg.secret} is empty" >&2
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
      secretPlaceholders = lib.attrNames templateCfg.substitutions;
      literalPlaceholders = lib.attrNames templateCfg.literalSubstitutions;
      gomplateExpression = gomplateRenderExpression secretPlaceholders literalPlaceholders;
      fetchLines = lib.concatStringsSep "\n" (lib.imap0 (idx: placeholder:
        let
          secretCfg = templateCfg.substitutions.${placeholder};
        in ''
          ${secretFetchCommand secretCfg} > "$template_workdir/secret-${toString idx}"
        ''
      ) secretPlaceholders);
      literalLines = lib.concatStringsSep "\n" (lib.imap0 (idx: placeholder: ''
        printf '%s' ${lib.escapeShellArg templateCfg.literalSubstitutions.${placeholder}} > "$template_workdir/literal-${toString idx}"
      '') literalPlaceholders);
    in ''
      dest=${lib.escapeShellArg templateCfg.destination}
      if ${if templateCfg.createOnly then "[ -e \"$dest\" ]" else "false"}; then
        :
      else
        template_workdir="$workdir/template-${sanitizeUnitName opId}"
        mkdir -p "$template_workdir"

        ${fetchLines}
        ${literalLines}
        ${gomplateDatasourceArgs "${templateCfg.source}" secretPlaceholders literalPlaceholders}

        ${pkgs.gomplate}/bin/gomplate "''${datasource_args[@]}" -i ${gomplateExpression} > "$template_workdir/rendered"

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
      unitConfig.ConditionPathExists = "/var/lib/proxnix/runtime/bin/proxnix-secrets";
      serviceConfig = {
        Type = "oneshot";
        UMask = "0077";
        Environment = [ "HOME=/root" ];
      } // lib.optionalAttrs hasMaterializedState {
        RemainAfterExit = true;
      };
      path = [ pkgs.coreutils ] ++ allRuntimeInputs;
      script = ''
        set -euo pipefail

        workdir="$(mktemp -d /run/proxnix-secret-unit-${sanitizeUnitName unit}.XXXXXX)"
        trap 'rm -rf "$workdir"' EXIT

        ${scriptBody}
      '';
    };
in {

  options.proxnix.common = lib.mkOption {
    type = lib.types.submodule {
      options = commonOptions;
    };
    default = {};
    description = lib.mdDoc ''
      Shared proxnix guest baseline.
    '';
  };

  options.proxnix.secrets = lib.mkOption {
    type = lib.types.attrsOf publicSecretType;
    default = {};
    description = lib.mdDoc ''
      Public proxnix-managed secret declarations.
    '';
  };

  options.proxnix.configs = lib.mkOption {
    type = lib.types.attrsOf publicConfigType;
    default = {};
    description = lib.mdDoc ''
      Public proxnix-managed rendered config declarations.
    '';
  };

  options.proxnix._internal = lib.mkOption {
    default = {};
    description = lib.mdDoc ''
      Internal proxnix plumbing and compatibility hooks.
    '';
    type = lib.types.submodule {
      options = {
        secrets = lib.mkOption {
          type = lib.types.submodule {
            options = lowLevelSecretsOptions;
          };
          default = {};
          description = lib.mdDoc ''
            Internal low-level secret/template engine.
          '';
        };

        configTemplateSources = lib.mkOption {
          type = lib.types.attrsOf lib.types.path;
          default = {};
          description = lib.mdDoc ''
            Internal registry mapping logical config source names to
            template files.
          '';
        };
      };
    };
  };

  config = lib.mkMerge [

    {
      proxnix._internal.secrets.files = publicCompatFileSecrets;
      proxnix._internal.secrets.templates = publicCompatTemplates;

      assertions = publicModelAssertions;
    }

    (lib.mkIf cfg.enable {
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
        // lib.optionalAttrs (cfg.adminPasswordHash == null && cfg.adminPasswordHashSecretName == null) {
          hashedPassword = "!";
        };
    })

    (lib.mkIf (cfg.enable && cfg.adminPasswordHash == null && cfg.adminPasswordHashSecretName != null) {
      proxnix._internal.secrets.oneshot.proxnix-common-admin-password = {
        description = "Apply proxnix admin password hash";
        secret = cfg.adminPasswordHashSecretName;
        optional = true;
        wantedBy = [ "multi-user.target" ];
        runtimeInputs = [ pkgs.shadow ];
        script = ''
          hash="$(tr -d '\r\n' < "$PROXNIX_SECRET_FILE")"
          if [ -z "$hash" ]; then
            echo "proxnix-common-admin-password: decrypted hash is empty" >&2
            exit 1
          fi

          printf '%s:%s\n' ${lib.escapeShellArg cfg.adminUser} "$hash" | chpasswd -e
        '';
      };
    })

    (lib.mkIf cfg.enable {
      assertions =
        invalidManagedTemplateAssertions
        ++ invalidActivationFileAssertions
        ++ invalidServiceFileAssertions
        ++ invalidActivationTemplateAssertions
        ++ invalidServiceTemplateAssertions
        ++ invalidCreateOnlyTemplateAssertions;

      systemd.services =
        (lib.mapAttrs' mkSecretUnitService secretOpsByUnit)
        // activationFileServiceConfigs
        // activationTemplateServiceConfigs
        // serviceSecretServiceConfigs
        // publicRuntimeSecretUnitConfigs
        // publicRuntimeSecretServiceConfigs;
    })

    (lib.mkIf (cfg.enable && cfg.enableTimesyncd) {
      services.timesyncd.enable = true;
    })

    (lib.mkIf (cfg.enable && cfg.manageJournald) {
      services.journald.extraConfig = ''
        SystemMaxUse=${cfg.journaldSystemMaxUse}
        RuntimeMaxUse=${cfg.journaldRuntimeMaxUse}
        MaxRetentionSec=${cfg.journaldMaxRetentionSec}
      '';
    })

    (lib.mkIf (cfg.enable && cfg.manageSwappiness) {
      boot.kernel.sysctl."vm.swappiness" = cfg.vmSwappiness;
    })

  ];

}
