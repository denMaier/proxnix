{
  description = "proxnix host and workstation tooling";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      lib = nixpkgs.lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = lib.genAttrs systems;
    in {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          workstation = pkgs.callPackage ./nix/packages/cli { };
          managerWeb = pkgs.callPackage ./nix/packages/manager-web { };
        in {
          default = workstation.tui;
          proxnix-workstation = workstation.tui;
          proxnix-workstation-cli = workstation.cli;
          proxnix-manager-web = managerWeb;
        });

      overlays.default = final: prev:
        let
          workstation = final.callPackage ./nix/packages/cli { };
          managerWeb = final.callPackage ./nix/packages/manager-web { };
        in {
          proxnix-workstation = workstation.tui;
          proxnix-workstation-cli = workstation.cli;
          proxnix-manager-web = managerWeb;
        };

      nixosModules.proxnix-workstation = import ./nix/modules/proxnix-workstation.nix;
      nixosModules.proxnix-manager-web = import ./nix/modules/proxnix-manager-web.nix;
      darwinModules.proxnix-workstation = import ./nix/modules/proxnix-workstation.nix;
    };
}
