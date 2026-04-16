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
    (managedDir + "/security-policy.nix")
  ] ++ siteImports ++ [
    (managedDir + "/proxmox.nix")
  ] ++ dropins ++ localImports;

  # Import order documents the intended layering, but it is not a complete
  # precedence system. Proxnix-owned modules should use mkDefault/mkForce where
  # needed; site/local modules still need explicit override priorities if they
  # both set the same non-mergeable option. Treat /etc/nixos/local.nix as a
  # guest-local experimentation layer, not a trusted security-policy source.
  # Podman is disabled automatically when no Quadlet unit files are staged.

  system.stateVersion = "25.11";
}
