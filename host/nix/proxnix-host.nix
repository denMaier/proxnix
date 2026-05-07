{ lib
, stdenvNoCC
, proxnixHostController
}:

stdenvNoCC.mkDerivation {
  pname = "proxnix-host";
  version = lib.strings.trim (builtins.readFile ../../VERSION);

  src =
    let
      root = ../..;
      fs = lib.fileset;
    in
    fs.toSource {
      inherit root;
      fileset = fs.intersection (fs.gitTracked root) (fs.unions [
        ../../host/install
        ../../host/runtime
      ]);
    };

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/bin" "$out/lib/proxnix" "$out/share/proxnix" "$out/share/systemd/system"

    cp -R host/runtime/bin/. "$out/bin/"
    cp ${proxnixHostController}/bin/proxnix-host "$out/bin/proxnix-host"
    cp ${proxnixHostController}/bin/proxnix-secrets-guest "$out/lib/proxnix/proxnix-secrets-guest"
    cp host/install/uninstall.sh "$out/bin/proxnix-host-uninstall"
    chmod +x "$out"/bin/*
    chmod 755 "$out/lib/proxnix/proxnix-secrets-guest"

    mkdir -p "$out/share/proxnix/lxc/config" "$out/share/proxnix/lxc/hooks" "$out/share/proxnix/nix"
    cp -R host/runtime/lxc/config/. "$out/share/proxnix/lxc/config/"
    ln -s ../../../../bin/proxnix-host "$out/share/proxnix/lxc/hooks/nixos-proxnix-start-host"

    cp -R host/runtime/nix/. "$out/share/proxnix/nix/"
    cp -R host/runtime/systemd/. "$out/share/systemd/system/"

    runHook postInstall
  '';

  meta = {
    description = "Host-side proxnix runtime for Proxmox nodes";
    platforms = lib.platforms.linux;
  };
}
