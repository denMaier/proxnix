let
  managedDir = /etc/nixos/managed;
  dropinDir = managedDir + "/dropins";
  siteOverride = managedDir + "/site.nix";
  localOverride = /etc/nixos/local.nix;
  dropins =
    if builtins.pathExists dropinDir
    then map (f: dropinDir + "/${f}")
         (builtins.filter (f: builtins.match ".*\\.nix" f != null)
          (builtins.attrNames (builtins.readDir dropinDir)))
    else [];
  siteImports =
    if builtins.pathExists siteOverride
    then [ siteOverride ]
    else [];
  localImports =
    if builtins.pathExists localOverride
    then [ localOverride ]
    else [];
in {
  imports = [
    (managedDir + "/base.nix")
    (managedDir + "/common.nix")
  ] ++ siteImports ++ [
    (managedDir + "/proxmox.nix")
    (managedDir + "/user.nix")
  ] ++ dropins ++ localImports;

  # Podman is disabled automatically when no Quadlet unit files are staged.

  system.stateVersion = "25.05";
}
