{ lib
, rustPlatform
}:

rustPlatform.buildRustPackage rec {
  pname = "proxnix-host-controller";
  version = lib.strings.trim (builtins.readFile ../../VERSION);

  src = ../..;
  cargoLock.lockFile = ../../Cargo.lock;
  PROXNIX_VERSION = version;

  meta = {
    description = "Rust host-side proxnix control binary";
    platforms = lib.platforms.unix;
    mainProgram = "proxnix-host";
  };
}
