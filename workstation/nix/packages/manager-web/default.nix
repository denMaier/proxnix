{ lib
, stdenvNoCC
, makeWrapper
, callPackage
, bun
, python3
, sops
, openssh
, rsync
, git
}:

let
  workstationPackages = callPackage ../cli { };
  workstationCli = workstationPackages.cli;
  pythonEnv = python3.withPackages (ps: [
    ps.cryptography
  ]);
  runtimeInputs = [
    workstationCli
    pythonEnv
    sops
    openssh
    rsync
    git
  ];
in
stdenvNoCC.mkDerivation {
  pname = "proxnix-manager-web";
  version = "unstable";
  src = ../../../manager;

  nativeBuildInputs = [ makeWrapper ];

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/share/proxnix-manager-web"
    cp -a app package.json "$out/share/proxnix-manager-web/"

    mkdir -p "$out/bin"
    makeWrapper ${bun}/bin/bun "$out/bin/proxnix-manager-web" \
      --add-flags "run $out/share/proxnix-manager-web/app/webui/index.ts" \
      --prefix PATH : ${lib.makeBinPath runtimeInputs} \
      --set-default PROXNIX_MANAGER_PYTHON ${pythonEnv}/bin/python \
      --prefix PROXNIX_MANAGER_PYTHONPATH : ${workstationCli}/share/proxnix-workstation/src

    runHook postInstall
  '';

  meta = {
    description = "Proxnix Manager hosted web UI";
    homepage = "https://github.com/denMaier/proxnix";
    license = lib.licenses.mit;
    platforms = lib.platforms.unix;
    mainProgram = "proxnix-manager-web";
  };
}
