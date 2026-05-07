{ lib
, rustPlatform
, age
, openssh
}:

rustPlatform.buildRustPackage rec {
  pname = "proxnix-host-controller";
  version = lib.strings.trim (builtins.readFile ../../VERSION);

  src =
    let
      root = ../..;
      fs = lib.fileset;
    in
    fs.toSource {
      inherit root;
      fileset = fs.intersection (fs.gitTracked root) (fs.unions [
        ../../Cargo.toml
        ../../Cargo.lock
        ../../crates/proxnix-host
      ]);
  };
  cargoLock.lockFile = ../../Cargo.lock;
  nativeCheckInputs = [
    age
    openssh
  ];
  PROXNIX_VERSION = version;

  meta = {
    description = "Rust host-side proxnix control binary";
    platforms = lib.platforms.unix;
    mainProgram = "proxnix-host";
  };
}
