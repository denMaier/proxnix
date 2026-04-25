{ config, lib, pkgs, ... }:

let
  cfg = config.proxnix.manager.web;
  managerPackage = pkgs.callPackage ../packages/manager-web { };
  listenUrl = "http://${cfg.host}:${toString cfg.port}";
  nginxEnabled = cfg.deploymentMode == "reverse-proxy";
  hasAuthRequest = cfg.reverseProxy.authRequestUrl != "";
  trustedUserHeaderVar =
    builtins.replaceStrings [ "-" ] [ "_" ] (lib.toLower cfg.reverseProxy.trustedUserHeader);
  trustedUserValue =
    if hasAuthRequest
    then "$proxnix_auth_user"
    else "$http_${trustedUserHeaderVar}";
in {
  options.proxnix.manager.web = {
    enable = lib.mkEnableOption "Proxnix Manager web service";

    deploymentMode = lib.mkOption {
      type = lib.types.enum [ "local" "reverse-proxy" "direct" ];
      default = "local";
      description = lib.mdDoc ''
        Deployment mode for Proxnix Manager web.

        `local` binds the app to loopback only and is meant for local use,
        SSH tunnels, or an independently managed proxy.

        `reverse-proxy` binds the app to loopback and configures nginx in
        front of it. Configure `reverseProxy.authRequestUrl` so nginx delegates
        authentication to an auth proxy such as Authelia, Authentik,
        oauth2-proxy, or another service that implements nginx `auth_request`.

        `direct` exposes the Bun web server without a reverse auth proxy. This
        is intended only for isolated development networks and requires
        `dangerouslyAllowDirect = true`.
      '';
    };

    dangerouslyAllowDirect = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = lib.mdDoc ''
        Required acknowledgement for `deploymentMode = "direct"`.
      '';
    };

    package = lib.mkOption {
      type = lib.types.package;
      default = managerPackage;
      defaultText = lib.literalExpression "inputs.proxnix.packages.${pkgs.system}.proxnix-manager-web";
      description = lib.mdDoc ''
        Package that provides `proxnix-manager-web`. The default package wraps
        the Manager web server with the Nix-provided `proxnix-workstation-cli`
        package, a Python environment suitable for the Manager bridge, and the
        runtime tools needed by publish/secrets/git workflows.
      '';
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "proxnix-manager";
      description = lib.mdDoc "User that runs the manager web service.";
    };

    group = lib.mkOption {
      type = lib.types.str;
      default = "proxnix-manager";
      description = lib.mdDoc "Group that runs the manager web service.";
    };

    createUser = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = lib.mdDoc "Create the configured service user and group.";
    };

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = lib.mdDoc "Host address for the Bun web server.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 4173;
      description = lib.mdDoc "Port for the Bun web server.";
    };

    configHome = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/proxnix-manager/.config";
      description = lib.mdDoc ''
        XDG config home used by the Manager service. The workstation config is
        expected at `configHome/proxnix/config`.
      '';
    };

    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      example = {
        PROXNIX_MANAGER_PYTHONPATH = "/opt/proxnix/provider-python/lib/python3.12/site-packages";
      };
      description = lib.mdDoc "Extra environment variables for the Manager service.";
    };

    extraReadWritePaths = lib.mkOption {
      type = lib.types.listOf lib.types.path;
      default = [ ];
      example = [ "/srv/proxnix-site" ];
      description = lib.mdDoc ''
        Additional paths the systemd service may write. Add the configured
        Proxnix site repo here if Manager should create bundles, edit metadata,
        stage git changes, or publish from the hosted service.
      '';
    };

    reverseProxy = {
      serverName = lib.mkOption {
        type = lib.types.str;
        default = "";
        example = "proxnix.example.com";
        description = lib.mdDoc "nginx virtual host name for reverse-proxy mode.";
      };

      enableACME = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = lib.mdDoc "Enable ACME certificates for the nginx virtual host.";
      };

      forceSSL = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = lib.mdDoc "Redirect HTTP to HTTPS for the nginx virtual host.";
      };

      authRequestUrl = lib.mkOption {
        type = lib.types.str;
        default = "";
        example = "http://127.0.0.1:4180/oauth2/auth";
        description = lib.mdDoc ''
          Internal auth-check endpoint used by nginx `auth_request`. Leave empty
          only if another proxy layer in front of nginx already authenticates
          users.
        '';
      };

      signInUrl = lib.mkOption {
        type = lib.types.str;
        default = "";
        example = "https://auth.example.com/oauth2/start?rd=$scheme://$host$request_uri";
        description = lib.mdDoc "URL nginx returns for 401 responses from the auth endpoint.";
      };

      trustedUserHeader = lib.mkOption {
        type = lib.types.str;
        default = "X-Forwarded-User";
        description = lib.mdDoc ''
          Header set by the trusted auth proxy and exposed to Proxnix Manager as
          the display identity.
        '';
      };

      extraConfig = lib.mkOption {
        type = lib.types.lines;
        default = "";
        description = lib.mdDoc "Extra nginx location config for the proxied app.";
      };
    };
  };

  config = lib.mkIf cfg.enable (lib.mkMerge [
    {
      assertions = [
        {
          assertion = cfg.deploymentMode != "direct" || cfg.dangerouslyAllowDirect;
          message = ''
            proxnix.manager.web.deploymentMode = "direct" exposes Proxnix Manager without
            a reverse auth proxy. Set proxnix.manager.web.dangerouslyAllowDirect = true
            only for isolated development networks.
          '';
        }
        {
          assertion = cfg.deploymentMode != "reverse-proxy" || cfg.reverseProxy.serverName != "";
          message = "proxnix.manager.web.reverseProxy.serverName is required in reverse-proxy mode.";
        }
      ];

      users.groups = lib.mkIf cfg.createUser {
        ${cfg.group} = { };
      };

      users.users = lib.mkIf cfg.createUser {
        ${cfg.user} = {
          inherit (cfg) group;
          isSystemUser = true;
          home = "/var/lib/proxnix-manager";
          createHome = true;
        };
      };

      systemd.services.proxnix-manager-web = {
        description = "Proxnix Manager web UI";
        wantedBy = [ "multi-user.target" ];
        after = [ "network-online.target" ];
        wants = [ "network-online.target" ];

        environment = {
          XDG_CONFIG_HOME = toString cfg.configHome;
          PROXNIX_MANAGER_WEB_HOST = cfg.host;
          PROXNIX_MANAGER_WEB_PORT = toString cfg.port;
        } // lib.optionalAttrs nginxEnabled {
          PROXNIX_MANAGER_TRUSTED_AUTH_HEADER = cfg.reverseProxy.trustedUserHeader;
        } // cfg.environment;

        serviceConfig = {
          ExecStart = "${lib.getExe cfg.package} --host ${cfg.host} --port ${toString cfg.port}";
          Restart = "on-failure";
          RestartSec = "3s";
          User = cfg.user;
          Group = cfg.group;
          StateDirectory = "proxnix-manager";
          WorkingDirectory = "/var/lib/proxnix-manager";
          NoNewPrivileges = true;
          PrivateTmp = true;
          ProtectSystem = "strict";
          ProtectHome = "read-only";
          ReadWritePaths = [
            "/var/lib/proxnix-manager"
            (toString cfg.configHome)
          ] ++ map toString cfg.extraReadWritePaths;
        };
      };
    }

    (lib.mkIf nginxEnabled {
      services.nginx = {
        enable = true;
        recommendedProxySettings = true;
        recommendedTlsSettings = true;

        virtualHosts.${cfg.reverseProxy.serverName} = {
          enableACME = cfg.reverseProxy.enableACME;
          forceSSL = cfg.reverseProxy.forceSSL;

          locations."/" = {
            proxyPass = listenUrl;
            proxyWebsockets = true;
            extraConfig = ''
              ${lib.optionalString hasAuthRequest "auth_request /_proxnix_auth;"}
              ${lib.optionalString hasAuthRequest "auth_request_set $proxnix_auth_user $upstream_http_${trustedUserHeaderVar};"}
              proxy_set_header ${cfg.reverseProxy.trustedUserHeader} ${trustedUserValue};
              ${lib.optionalString (cfg.reverseProxy.signInUrl != "") "error_page 401 = ${cfg.reverseProxy.signInUrl};"}
              ${cfg.reverseProxy.extraConfig}
            '';
          };

          locations."/_proxnix_auth" = lib.mkIf hasAuthRequest {
            proxyPass = cfg.reverseProxy.authRequestUrl;
            extraConfig = ''
              internal;
              proxy_pass_request_body off;
              proxy_set_header Content-Length "";
              proxy_set_header X-Original-URI $request_uri;
              proxy_set_header X-Original-Method $request_method;
            '';
          };
        };
      };
    })
  ]);
}
