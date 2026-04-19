{ pkgs, ... }:

let
  siteRoot = "/var/lib/nginx-demo/www";
in {
  services.nginx = {
    enable = true;
    virtualHosts."proxnix-nginx-native" = {
      default = true;
      listen = [
        {
          addr = "0.0.0.0";
          port = 8080;
        }
      ];
      root = siteRoot;
      locations."/".tryFiles = "$uri $uri/ /index.html";
    };
  };

  networking.firewall.allowedTCPPorts = [ 8080 ];

  systemd.tmpfiles.rules = [
    "d ${siteRoot} 0755 root root -"
  ];

  proxnix.secrets.templates.nginx-index = {
    source = pkgs.writeText "nginx-index.html" ''
      <!doctype html>
      <html>
        <body>
          <h1>__NGINX_INDEX_MESSAGE__</h1>
        </body>
      </html>
    '';
    destination = "${siteRoot}/index.html";
    owner = "root";
    group = "root";
    mode = "0644";
    restartUnits = [ "nginx.service" ];
    substitutions = {
      "__NGINX_INDEX_MESSAGE__" = {
        secret = "nginx_index_message";
      };
    };
  };
}
