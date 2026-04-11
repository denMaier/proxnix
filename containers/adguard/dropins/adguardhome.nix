{ lib, pkgs, ... }:

let
  workDir = "/opt/adguard";
  configFile = "${workDir}/AdGuardHome.yaml";
  seedConfig = ./adguard/AdGuardHome.yaml;
  adminPasswordHashSecret = "common_adguard_admin_password_hash";
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
    requires = [ "proxnix-adguardhome-seed.service" ];
    after = [ "network-online.target" "proxnix-adguardhome-seed.service" ];

    serviceConfig = {
      DynamicUser = lib.mkForce false;
      User = "adguardhome";
      Group = "adguardhome";
      WorkingDirectory = workDir;
      ReadWritePaths = [ workDir ];
      ExecStart = lib.mkForce "${pkgs.adguardhome}/bin/AdGuardHome --no-check-update --pidfile /run/AdGuardHome/AdGuardHome.pid --work-dir ${workDir} --config ${configFile}";
    };
  };

  systemd.services.proxnix-adguardhome-seed = {
    description = "Seed mutable AdGuard Home configuration from proxnix secrets";
    before = [ "adguardhome.service" ];
    after = [ "local-fs.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [
      pkgs.coreutils
      pkgs.gnugrep
      pkgs.gnused
    ];
    script = ''
      set -euo pipefail

      if [ -e ${configFile} ] && ! grep -q '__PROXNIX_ADGUARD_ADMIN_PASSWORD_HASH__' ${configFile}; then
        exit 0
      fi

      install -d -m 0750 -o adguardhome -g adguardhome ${workDir}
      if [ ! -x /usr/local/bin/proxnix-secrets ]; then
        echo "proxnix-adguardhome-seed: /usr/local/bin/proxnix-secrets is missing" >&2
        exit 1
      fi

      hash="$(/usr/local/bin/proxnix-secrets get-shared ${lib.escapeShellArg adminPasswordHashSecret} | tr -d '\r\n')"
      if [ -z "$hash" ]; then
        echo "proxnix-adguardhome-seed: shared secret ${adminPasswordHashSecret} is empty" >&2
        exit 1
      fi

      tmp="$(mktemp ${workDir}/AdGuardHome.yaml.XXXXXX)"
      trap 'rm -f "$tmp"' EXIT

      sed "s|__PROXNIX_ADGUARD_ADMIN_PASSWORD_HASH__|$hash|g" ${seedConfig} > "$tmp"
      chown adguardhome:adguardhome "$tmp"
      chmod 0600 "$tmp"
      mv "$tmp" ${configFile}
      trap - EXIT
    '';
  };
}
