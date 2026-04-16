{ lib, pkgs, ... }:

let
  workDir = "/opt/adguard";
  configFile = "${workDir}/AdGuardHome.yaml";
  seedConfig = ./adguard/AdGuardHome.yaml;
  adminPasswordHashSecret = "common_adguard_admin_password_hash";
  seedUnit = "proxnix-secret-template-adguardhome";
in {
  services.adguardhome = {
    enable = true;
    package = pkgs.adguardhome;
    host = "0.0.0.0";
    port = 3000;
    openFirewall = true;

    # AdGuard Home Sync and the web UI need to persist changes made at runtime.
    # Keep this null so the NixOS module does not merge declarative settings
    # over the mutable AdGuardHome.yaml on every service start.
    mutableSettings = true;
    settings = null;
  };

  networking.firewall.allowedTCPPorts = [ 53 ];
  networking.firewall.allowedUDPPorts = [ 53 ];

  users.groups.adguardhome = {};
  users.users.adguardhome = {
    isSystemUser = true;
    group = "adguardhome";
    home = workDir;
  };

  systemd.tmpfiles.rules = [
    "d ${workDir} 0750 adguardhome adguardhome -"
    "Z ${workDir} 0750 adguardhome adguardhome -"
  ];

  systemd.services.adguardhome = {
    wants = [ "network-online.target" ];
    requires = [ seedUnit ];
    after = [ "network-online.target" seedUnit ];

    serviceConfig = {
      DynamicUser = lib.mkForce false;
      User = "adguardhome";
      Group = "adguardhome";
      WorkingDirectory = workDir;
      ReadWritePaths = [ workDir ];
      ExecStart = lib.mkForce "${pkgs.adguardhome}/bin/AdGuardHome --no-check-update --pidfile /run/AdGuardHome/AdGuardHome.pid --work-dir ${workDir} --config ${configFile}";
    };
  };

  proxnix.secrets.templates.adguardhome = {
    description = "Seed mutable AdGuard Home configuration from proxnix secrets";
    unit = seedUnit;
    source = seedConfig;
    destination = configFile;
    owner = "adguardhome";
    group = "adguardhome";
    mode = "0600";
    createOnly = true;
    before = [ "adguardhome.service" ];
    substitutions = {
      "__PROXNIX_ADGUARD_ADMIN_PASSWORD_HASH__" = {
        secret = adminPasswordHashSecret;
        lookup = "get-shared";
      };
    };
    runtimeInputs = [
      pkgs.coreutils
    ];
  };
}
