{ config, lib, pkgs, ... }:

let
  workstationPackages = pkgs.callPackage ../packages/workstation { };
  cfg = config.proxnix.workstation;
in {
  options.proxnix.workstation = {
    enable = lib.mkEnableOption "proxnix workstation tooling";

    package = lib.mkOption {
      type = lib.types.package;
      default = workstationPackages.tui;
      defaultText = lib.literalExpression "inputs.proxnix.packages.${pkgs.system}.proxnix-workstation";
      description = lib.mdDoc ''
        Package to install into the system profile. The default package includes
        the TUI plus all workstation CLI helpers. Use the `cli` package if you
        only want the shell tools.
      '';
    };

    extraPackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [ ];
      description = lib.mdDoc ''
        Additional packages appended to `environment.systemPackages` when the
        workstation module is enabled.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ] ++ cfg.extraPackages;
  };
}
