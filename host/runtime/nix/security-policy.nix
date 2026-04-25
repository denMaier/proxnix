{ config, lib, ... }:

let
  cfg = config.proxnix.common;
  inheritedRootAuthorizedKeys = config.users.users.root.openssh.authorizedKeys.keys or [];
  adminAuthorizedKeys =
    lib.unique (
      cfg.adminAuthorizedKeys
      ++ lib.optionals cfg.inheritRootAuthorizedKeys inheritedRootAuthorizedKeys
    );
in {
  # Trusted host-side security policy. This module is intentionally forceful so
  # guest-local /etc/nixos/local.nix cannot relax core access controls.

  proxnix.common = {
    enable = lib.mkForce true;
    adminPasswordHashSecretName = lib.mkForce "common_admin_password_hash";
    wheelNeedsPassword = lib.mkForce true;
    lockRootPassword = lib.mkForce true;
    permitRootLogin = lib.mkForce "prohibit-password";
  };

  networking.useHostResolvConf = lib.mkForce false;
  proxmoxLXC.manageHostName = lib.mkForce true;

  security.sudo = {
    enable = lib.mkForce true;
    wheelNeedsPassword = lib.mkForce cfg.wheelNeedsPassword;
  };

  users.users.${cfg.adminUser} =
    {
      openssh.authorizedKeys.keys = lib.mkForce adminAuthorizedKeys;
    }
    // lib.optionalAttrs (cfg.adminPasswordHash != null) {
      hashedPassword = lib.mkForce cfg.adminPasswordHash;
    }
    // lib.optionalAttrs (cfg.adminPasswordHash == null) {
      hashedPassword = lib.mkForce "!";
    };

  users.users.root.hashedPassword = lib.mkIf cfg.lockRootPassword (lib.mkForce "!");

  services.openssh = {
    enable = lib.mkForce true;
    settings = {
      PasswordAuthentication = lib.mkForce false;
      KbdInteractiveAuthentication = lib.mkForce false;
      ChallengeResponseAuthentication = lib.mkForce false;
      PermitEmptyPasswords = lib.mkForce false;
      PubkeyAuthentication = lib.mkForce true;
      X11Forwarding = lib.mkForce false;
      PermitRootLogin = lib.mkForce cfg.permitRootLogin;
    };
  };
}
