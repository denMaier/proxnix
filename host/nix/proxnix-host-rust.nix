{ lib
, rustPlatform
}:

rustPlatform.buildRustPackage {
  pname = "proxnix-host-rust";
  version = lib.strings.trim (builtins.readFile ../../VERSION);

  src = ../../host/rust;
  cargoLock.lockFile = ../../host/rust/Cargo.lock;

  meta = {
    description = "Rust host-side proxnix control binary";
    platforms = lib.platforms.unix;
    mainProgram = "proxnix-host";
  };
}
