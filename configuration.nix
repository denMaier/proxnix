let
  managedDir = /etc/nixos/managed;
  dropinDir = managedDir + "/dropins";
  localOverride = /etc/nixos/local.nix;
  dropins =
    if builtins.pathExists dropinDir
    then map (f: dropinDir + "/${f}")
         (builtins.filter (f: builtins.match ".*\\.nix" f != null)
          (builtins.attrNames (builtins.readDir dropinDir)))
    else [];
  localImports =
    if builtins.pathExists localOverride
    then [ localOverride ]
    else [];
in {
  imports = [
    (managedDir + "/chezmoi.nix")
    (managedDir + "/base.nix")
    (managedDir + "/common.nix")
    (managedDir + "/proxmox.nix")
    (managedDir + "/user.nix")
  ] ++ dropins ++ localImports;

  # To disable Podman on a native-services container (Jellyfin, Immich):
  #   virtualisation.podman.enable = lib.mkForce false;

  system.stateVersion = "25.05";
}
