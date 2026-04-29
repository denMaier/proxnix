{ lib
, stdenvNoCC
, age
, jq
, proxnixHostRust
, rsync
, sops
}:

stdenvNoCC.mkDerivation {
  pname = "proxnix-host";
  version = lib.strings.trim (builtins.readFile ../../VERSION);

  src = ../..;

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/bin" "$out/lib/proxnix" "$out/share/proxnix" "$out/share/systemd/system"

    cp -R host/runtime/bin/. "$out/bin/"
    cp ${proxnixHostRust}/bin/proxnix-host "$out/bin/proxnix-host"
    cp host/install/uninstall.sh "$out/bin/proxnix-host-uninstall"
    ln -s proxnix-host-uninstall "$out/bin/proxnix-uninstall"
    chmod +x "$out"/bin/*

    cp -R host/runtime/lib/. "$out/lib/proxnix/"
    cp host/runtime/lxc/hooks/nixos-proxnix-common.sh "$out/lib/proxnix/nixos-proxnix-common.sh"
    chmod +x "$out/lib/proxnix/proxnix-secrets-guest"
    chmod +x "$out/lib/proxnix/proxnix_authority_render.py" "$out/lib/proxnix/proxnix_reconciler_state.py"

    mkdir -p "$out/share/proxnix/lxc/config" "$out/share/proxnix/lxc/hooks" "$out/share/proxnix/nix"
    cp -R host/runtime/lxc/config/. "$out/share/proxnix/lxc/config/"
    cp host/runtime/lxc/hooks/nixos-proxnix-prestart "$out/share/proxnix/lxc/hooks/"
    cp host/runtime/lxc/hooks/nixos-proxnix-mount "$out/share/proxnix/lxc/hooks/"
    cp host/runtime/lxc/hooks/nixos-proxnix-poststop "$out/share/proxnix/lxc/hooks/"
    chmod +x "$out/share/proxnix/lxc/hooks/"*

    cp -R host/runtime/nix/. "$out/share/proxnix/nix/"
    cp -R host/runtime/systemd/. "$out/share/systemd/system/"

    ln -s ${age}/bin/age "$out/bin/age"
    ln -s ${jq}/bin/jq "$out/bin/jq"
    ln -s ${rsync}/bin/rsync "$out/bin/rsync"
    ln -s ${sops}/bin/sops "$out/bin/sops"

    runHook postInstall
  '';

  meta = {
    description = "Host-side proxnix runtime for Proxmox nodes";
    platforms = lib.platforms.linux;
  };
}
