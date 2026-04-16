{ config, lib, pkgs, ... }:

let
  versityDataDir = "/var/lib/versitygw/data";
  versityMetaDir = "/var/lib/versitygw/meta";
  secretDir = "/run/ente-secrets";
  secretUnit = "proxnix-ente-secrets";
  bucketNames = [
    "b2-eu-cen"
    "wasabi-eu-central-2-v3"
    "scw-eu-fr-v3"
  ];
  secretNames = [
    "ente-s3-user"
    "ente-s3-pass"
    "ente-museum-key"
    "ente-museum-hash"
    "ente-museum-jwt-secret"
  ];
  secretPathFor = name: "${secretDir}/${name}";
in {
  services.ente.api = {
    enable = true;
    domain = "api.photos.example.com";
    enableLocalDB = true;
    nginx.enable = true;

    settings = {
      log-file = "";
      s3 = {
        are_local_buckets = true;
        use_path_style_urls = true;

        b2-eu-cen = {
          endpoint = "127.0.0.1:3200";
          region = "eu-central-2";
          bucket = "b2-eu-cen";
          key._secret = secretPathFor "ente-s3-user";
          secret._secret = secretPathFor "ente-s3-pass";
        };

        wasabi-eu-central-2-v3 = {
          endpoint = "127.0.0.1:3200";
          region = "eu-central-2";
          bucket = "wasabi-eu-central-2-v3";
          compliance = false;
          key._secret = secretPathFor "ente-s3-user";
          secret._secret = secretPathFor "ente-s3-pass";
        };

        scw-eu-fr-v3 = {
          endpoint = "127.0.0.1:3200";
          region = "eu-central-2";
          bucket = "scw-eu-fr-v3";
          key._secret = secretPathFor "ente-s3-user";
          secret._secret = secretPathFor "ente-s3-pass";
        };
      };

      key = {
        encryption._secret = secretPathFor "ente-museum-key";
        hash._secret = secretPathFor "ente-museum-hash";
      };

      jwt.secret._secret = secretPathFor "ente-museum-jwt-secret";
    };
  };

  services.ente.web = {
    enable = true;
    domains = {
      api = "api.photos.example.com";
      accounts = "accounts.photos.example.com";
      cast = "cast.photos.example.com";
      albums = "albums.photos.example.com";
      photos = "photos.photos.example.com";
    };
  };

  users.groups.ente-secrets = {};
  users.groups.versitygw = {};
  users.users.ente.extraGroups = [ "ente-secrets" ];
  users.users.versitygw = {
    isSystemUser = true;
    group = "versitygw";
    home = "/var/lib/versitygw";
    createHome = false;
    extraGroups = [ "ente-secrets" ];
  };

  systemd.tmpfiles.rules = [
    "d ${secretDir} 0750 root ente-secrets -"
    "d ${versityDataDir} 0750 versitygw versitygw -"
    "d ${versityMetaDir} 0750 versitygw versitygw -"
  ];

  proxnix.secrets.files =
    lib.listToAttrs (map (name: {
      inherit name;
      value = {
        unit = secretUnit;
        path = secretPathFor name;
        owner = "root";
        group = "ente-secrets";
        mode = "0640";
        before = [
          "versitygw.service"
          "ente.service"
          "proxnix-ente-buckets.service"
        ];
        wantedBy = [
          "versitygw.service"
          "ente.service"
          "proxnix-ente-buckets.service"
        ];
      };
    }) secretNames);

  systemd.services.${secretUnit} = {
    description = lib.mkForce "Materialize proxnix secrets for native Ente services";
  };

  systemd.services.versitygw = {
    description = "Versity S3 gateway for Ente";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" "${secretUnit}.service" ];
    requires = [ "${secretUnit}.service" ];
    serviceConfig = {
      User = "versitygw";
      Group = "versitygw";
      ExecStart = "${pkgs.runtimeShell} -euc 'export ROOT_ACCESS_KEY=\"$(tr -d \"\\r\\n\" < ${secretPathFor "ente-s3-user"})\"; export ROOT_SECRET_KEY=\"$(tr -d \"\\r\\n\" < ${secretPathFor "ente-s3-pass"})\"; exec ${lib.getExe pkgs.versitygw} --port :3200 posix --sidecar ${versityMetaDir} ${versityDataDir}'";
      Restart = "on-failure";
      RestartSec = "5s";
      WorkingDirectory = "/var/lib/versitygw";
      StateDirectory = "versitygw";
      AmbientCapabilities = [ ];
      CapabilityBoundingSet = [ ];
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      NoNewPrivileges = true;
      PrivateDevices = true;
      PrivateTmp = true;
      ProtectClock = true;
      ProtectControlGroups = true;
      ProtectHome = true;
      ProtectHostname = true;
      ProtectKernelLogs = true;
      ProtectKernelModules = true;
      ProtectKernelTunables = true;
      ProtectSystem = "strict";
      ReadWritePaths = [ "/var/lib/versitygw" ];
      RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
      RestrictNamespaces = true;
      RestrictRealtime = true;
      RestrictSUIDSGID = true;
      SystemCallArchitectures = "native";
      SystemCallFilter = "@system-service";
      UMask = "0077";
    };
  };

  systemd.services.proxnix-ente-buckets = {
    description = "Ensure Versity buckets exist for Ente";
    wantedBy = [ "multi-user.target" ];
    after = [ "versitygw.service" "${secretUnit}.service" ];
    requires = [ "versitygw.service" "${secretUnit}.service" ];
    before = [ "ente.service" ];
    path = [
      pkgs.awscli2
      pkgs.coreutils
      pkgs.curl
    ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      set -euo pipefail

      export AWS_ACCESS_KEY_ID="$(tr -d '\r\n' < ${secretPathFor "ente-s3-user"})"
      export AWS_SECRET_ACCESS_KEY="$(tr -d '\r\n' < ${secretPathFor "ente-s3-pass"})"

      until curl --fail --silent http://127.0.0.1:3200 >/dev/null 2>&1; do
        sleep 1
      done

      ${lib.concatMapStringsSep "\n" (bucket: ''
        aws --endpoint-url http://127.0.0.1:3200 s3api create-bucket --bucket ${lib.escapeShellArg bucket} || true
      '') bucketNames}
    '';
  };

  systemd.services.ente = {
    after = [ "${secretUnit}.service" "proxnix-ente-buckets.service" "versitygw.service" ];
    requires = [ "${secretUnit}.service" "proxnix-ente-buckets.service" "versitygw.service" ];
  };

  networking.firewall.allowedTCPPorts = [ 80 443 ];
}
