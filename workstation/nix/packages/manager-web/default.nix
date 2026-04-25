{ lib
, stdenvNoCC
, makeWrapper
, bun
, python3
, sops
, openssh
, rsync
, git
}:

stdenvNoCC.mkDerivation {
  pname = "proxnix-manager-web";
  version = "unstable";
  src = ../../../apps/proxnix-manager-electrobun;

  nativeBuildInputs = [ makeWrapper ];

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/share/proxnix-manager-web"
    cp -a src package.json "$out/share/proxnix-manager-web/"

    mkdir -p "$out/bin"
    makeWrapper ${bun}/bin/bun "$out/bin/proxnix-manager-web" \
      --add-flags "run $out/share/proxnix-manager-web/src/webserver/index.ts" \
      --prefix PATH : ${lib.makeBinPath [ python3 sops openssh rsync git ]}

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
