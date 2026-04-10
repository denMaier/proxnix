let
  # Dynamically import every *.nix file dropped into /etc/nixos/dropins/.
  # Files are pushed there by the pre-start hook from the host-side dropins/ folder.
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
    ./chezmoi.nix
    ./base.nix
    ./common.nix
    ./proxmox.nix
    ./user.nix
  ] ++ dropins;

  # To disable Podman on a native-services container (Jellyfin, Immich):
  #   virtualisation.podman.enable = lib.mkForce false;

  system.stateVersion = "25.05";
}
