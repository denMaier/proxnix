# Guest-side experimentation entry point. The host does **not** evaluate this
# file for actual reconciliation — the generated authority flake under
# /var/lib/proxnix/authority owns the desired closure. This file ships into
# the guest at /var/lib/proxnix/build-input/configuration.nix during host
# reconciliation so you can run a local nixos-rebuild against a clean
# snapshot of what the host evaluated:
#
#   1. Drop expressions into /var/lib/proxnix/build-input/local.nix
#   2. nixos-rebuild test -I nixos-config=/var/lib/proxnix/build-input/configuration.nix
#   3. Any activation you produce is reverted on the next host reconcile, and
#      build-input itself is rewritten by the Rust host controller on the next
#      reconcile. Save anything you want to keep into your site repo first.
#   4. Once the change works, promote it into a site dropin so the host owns
#      the durable state.
let
  managedDir = ./managed;
  dropinDir = managedDir + "/dropins";
  siteOverride = managedDir + "/site.nix";
  localOverride = ./local.nix;
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
  # precedence system. Proxnix-owned modules use mkDefault/mkForce where
  # needed; site/local modules still need explicit override priorities if they
  # both set the same non-mergeable option. Treat build-input/local.nix as a
  # guest-local experimentation layer, not a trusted security-policy source.
  # Podman and container workloads are owned by guest Nix config, not by
  # proxnix host-side staging.

  system.stateVersion = "25.11";
}
