{ lib
, stdenvNoCC
, makeWrapper
, bash
, openssh
, python3
, python3Packages
, rsync
, sops
}:

let
  version = "unstable";
  src = ../../../workstation;
  pythonEnv = python3.withPackages (ps: [
    ps.cryptography
  ]);
  runtimeInputs = [
    bash
    openssh
    pythonEnv
    rsync
    sops
  ];

  installRawFiles = scripts: ''
    mkdir -p "$out/bin"
    for script in ${lib.escapeShellArgs scripts}; do
      install -m 755 "$src/$script" "$out/bin/$(basename "$script")"
    done
  '';

  installPythonSources = ''
    mkdir -p "$out/share/proxnix-workstation"
    cp -a "$src/src" "$out/share/proxnix-workstation/src"
    install -m 644 "$src/pyproject.toml" "$out/share/proxnix-workstation/pyproject.toml"
    install -m 644 "$src/uv.lock" "$out/share/proxnix-workstation/uv.lock"
  '';

  writePythonWrapper = name: module: ''
    cat > "$out/bin/${name}" <<'EOF'
#!${bash}/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec env PYTHONPATH="$SCRIPT_DIR/../share/proxnix-workstation/src${PYTHONPATH:+:$PYTHONPATH}" ${pythonEnv}/bin/python -m ${module} "$@"
EOF
    chmod 755 "$out/bin/${name}"
  '';

  wrapScripts = scripts: ''
    for script in ${lib.escapeShellArgs scripts}; do
      wrapProgram "$out/bin/$script" \
        --prefix PATH : ${lib.makeBinPath runtimeInputs} \
        --prefix PYTHONPATH : "$out/share/proxnix-workstation/src"
    done
  '';

  mkWorkstationPackage = { pname, withTui ? false, mainProgram }:
    stdenvNoCC.mkDerivation {
      inherit pname version src;
      nativeBuildInputs = [ makeWrapper ];
      dontConfigure = true;
      dontBuild = true;

      installPhase =
        let
          scripts =
            [
              "legacy/proxnix-workstation-common.sh"
              "bin/proxnix"
              "bin/proxnix-publish"
              "bin/proxnix-secrets"
              "bin/proxnix-doctor"
              "bin/proxnix-lxc-exercise"
            ]
            ++ lib.optionals withTui [ "bin/proxnix-tui" ];
          wrappedScripts =
            [
              "proxnix"
              "proxnix-publish"
              "proxnix-secrets"
              "proxnix-doctor"
              "proxnix-lxc-exercise"
            ]
            ++ lib.optionals withTui [ "proxnix-tui" ];
        in
        ''
          runHook preInstall
          ${installRawFiles scripts}
          ${installPythonSources}
          ${writePythonWrapper "proxnix" "proxnix_workstation.cli"}
          ${writePythonWrapper "proxnix-publish" "proxnix_workstation.publish_cli"}
          ${writePythonWrapper "proxnix-secrets" "proxnix_workstation.secrets_cli"}
          ${writePythonWrapper "proxnix-doctor" "proxnix_workstation.doctor_cli"}
          ${writePythonWrapper "proxnix-lxc-exercise" "proxnix_workstation.exercise_cli"}
          ${writePythonWrapper "proxnix-tui" "proxnix_workstation.tui"}
          ${wrapScripts wrappedScripts}
          runHook postInstall
        '';

      meta = {
        description = "proxnix workstation CLI and TUI tools";
        homepage = "https://codeberg.org/maieretal/proxnix";
        license = lib.licenses.mit;
        platforms = lib.platforms.unix;
        inherit mainProgram;
      };
    };
in {
  cli = mkWorkstationPackage {
    pname = "proxnix-workstation-cli";
    mainProgram = "proxnix";
  };

  tui = mkWorkstationPackage {
    pname = "proxnix-workstation";
    withTui = true;
    mainProgram = "proxnix-tui";
  };
}
