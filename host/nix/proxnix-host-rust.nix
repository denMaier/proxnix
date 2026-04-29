{ lib
, rustPlatform
}:

rustPlatform.buildRustPackage rec {
  pname = "proxnix-host-rust";
  version = lib.strings.trim (builtins.readFile ../../VERSION);

  src = ../../host/rust;
  cargoLock.lockFile = ../../host/rust/Cargo.lock;
  PROXNIX_VERSION = version;

  meta = {
    description = "Rust host-side proxnix control binary";
    platforms = lib.platforms.unix;
    mainProgram = "proxnix-host";
  };
}
