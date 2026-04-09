let
  # quadlet-nix: Nix-native Quadlet container declarations.
  # Pin to a release tag + sha256 for reproducibility:
  #   url = "https://github.com/SEIAROTg/quadlet-nix/archive/refs/tags/vX.Y.Z.tar.gz";
  #   sha256 = "sha256:...";
  quadletNix = builtins.fetchTarball {
    url = "https://github.com/SEIAROTg/quadlet-nix/archive/main.tar.gz";
  };

  # Dynamically import every *.nix file dropped into /etc/nixos/dropins/.
  # Files are pushed there by the hookscript from the host-side dropins/ folder.
  # The directory may not exist on a brand-new container — that's fine.
  dropinDir = /etc/nixos/dropins;
  dropins =
    if builtins.pathExists dropinDir
    then map (f: dropinDir + "/${f}")
         (builtins.filter (f: builtins.match ".*\\.nix" f != null)
          (builtins.attrNames (builtins.readDir dropinDir)))
    else [];
in {
  imports = [
    "${quadletNix}/nixos-module.nix"
    ./chezmoi.nix
    ./base.nix
    ./proxmox.nix
    ./user.nix
  ] ++ dropins;

  # To disable Podman on a native-services container (Jellyfin, Immich):
  #   virtualisation.podman.enable = lib.mkForce false;

  system.stateVersion = "25.05";
}
