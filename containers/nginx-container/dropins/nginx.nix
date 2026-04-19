{ pkgs, ... }:

let
  siteRoot = "/var/lib/nginx-container-demo/html";
in {
  virtualisation.podman.enable = true;
  virtualisation.quadlet.enable = true;

  systemd.tmpfiles.rules = [
    "d ${siteRoot} 0755 root root -"
  ];

  proxnix.secrets.templates.nginx-index = {
    source = pkgs.writeText "nginx-container-index.html" ''
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
    substitutions = {
      "__NGINX_INDEX_MESSAGE__" = {
        secret = "nginx_index_message";
      };
    };
  };

  virtualisation.quadlet.containers.nginx = {
    autoStart = true;
    containerConfig = {
      image = "docker.io/library/nginx:latest";
      publishPorts = [ "127.0.0.1:8080:80" ];
      volumes = [ "${siteRoot}:/usr/share/nginx/html:ro" ];
    };
    serviceConfig.Restart = "always";
  };
}
