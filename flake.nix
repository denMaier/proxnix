{
  description = "proxnix host runtime";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      lib = nixpkgs.lib;
      hostSystems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      rustDevSystems = hostSystems ++ [
        "x86_64-darwin"
        "aarch64-darwin"
      ];

      forAllSystems = lib.genAttrs rustDevSystems;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          proxnix-host-rust = pkgs.callPackage ./host/nix/proxnix-host-rust.nix { };
        } // lib.optionalAttrs (lib.elem system hostSystems) {
          proxnix-host = pkgs.callPackage ./host/nix/proxnix-host.nix {
            proxnixHostRust = self.packages.${system}.proxnix-host-rust;
          };
          default = self.packages.${system}.proxnix-host;
        }
      );
    };
}
