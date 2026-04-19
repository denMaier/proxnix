# Proxnix Guest State Migration Plan

## Goal

Move all host-managed guest state out of rebuild-managed paths under `/etc` and into stable guest-owned paths under `/var/lib/proxnix`.

The only remaining host-mounted path under `/etc/nixos` will be `/etc/nixos/configuration.nix`, used purely as a bootstrap wrapper until the switched system installs its own managed wrapper.

## Target Rules

- Everything host-managed lives under `/var/lib/proxnix` inside the guest.
- Proxnix runtime state is never bind-mounted directly into `/etc/proxnix`, `/etc/systemd`, or `/usr/local/bin`.
- Proxnix secret handling expects its files under `/var/lib/proxnix/secrets`.
- The only file still mounted into `/etc/nixos` is `/etc/nixos/configuration.nix`.
- `/etc/nixos/configuration.nix` remains a thin wrapper that imports from `/var/lib/proxnix/config/...`.

## Target Layout

- `/var/lib/proxnix/config/configuration.nix`
- `/var/lib/proxnix/config/managed/base.nix`
- `/var/lib/proxnix/config/managed/common.nix`
- `/var/lib/proxnix/config/managed/security-policy.nix`
- `/var/lib/proxnix/config/managed/proxmox.nix`
- `/var/lib/proxnix/config/managed/dropins/...`
- `/var/lib/proxnix/runtime/current-config-hash`
- `/var/lib/proxnix/runtime/applied-config-hash`
- `/var/lib/proxnix/runtime/vmid`
- `/var/lib/proxnix/runtime/proxnix-apply-config-runner`
- `/var/lib/proxnix/runtime/systemd-attached/...`
- `/var/lib/proxnix/runtime/bin/...`
- `/var/lib/proxnix/runtime/manifests/...`
- `/var/lib/proxnix/secrets/effective.sops.yaml`
- `/var/lib/proxnix/secrets/identity`

## Configuration Wrapper

`/etc/nixos/configuration.nix` should stay a wrapper only. Its job is to import the host-managed config from `/var/lib/proxnix/config`.

The intended steady-state shape is:

```nix
let
  managedDir = /var/lib/proxnix/config/managed;
  dropinDir = managedDir + "/dropins";
  siteOverride = managedDir + "/site.nix";
  localOverride = /etc/nixos/local.nix;
  dropins =
    if builtins.pathExists dropinDir
    then map (f: dropinDir + "/${f}")
         (builtins.filter (f: builtins.match ".*\\.nix" f != null)
          (builtins.attrNames (builtins.readDir dropinDir)))
    else [];
  siteImports =
    if builtins.pathExists siteOverride
    then [ siteOverride ]
    else [];
  localImports =
    if builtins.pathExists localOverride
    then [ localOverride ]
    else [];
in {
  imports = [
    (managedDir + "/base.nix")
    (managedDir + "/common.nix")
    (managedDir + "/security-policy.nix")
  ] ++ siteImports ++ [
    (managedDir + "/proxmox.nix")
  ] ++ dropins ++ localImports;

  system.stateVersion = "25.11";
}
```

## Bootstrap Model

- The mount hook still stages `/etc/nixos/configuration.nix` for first boot.
- That file is the only disposable bootstrap bind mount under `/etc`.
- After `nixos-rebuild switch`, the system should ideally own `/etc/nixos/configuration.nix` itself as the same thin wrapper.
- If this works, losing the bootstrap bind mount after the switch is harmless.

## Open Risk

This part is still uncertain and needs to be tested before relying on it:

- NixOS may refuse to replace `/etc/nixos/configuration.nix` with a store-linked managed file if the bootstrap bind mount is still sitting there.
- If it does not fail on the first rebuild, it may still fail on the next rebuild.
- So the exact self-management behavior of `/etc/nixos/configuration.nix` is an explicit validation item, not an assumption.

## Migration Scope

These paths should stop being direct bind mounts into rebuild-managed locations:

- `/etc/proxnix/current-config-hash`
- `/etc/proxnix/applied-config-hash`
- `/etc/proxnix/vmid`
- `/etc/proxnix/proxnix-apply-config-runner`
- `/etc/proxnix/secrets/*`
- `/etc/proxnix/managed-*.list`
- `/etc/systemd/system.attached/*`
- `/usr/local/bin/*` for host-managed attached scripts
- `/etc/nixos/managed/*`

These should instead be staged under `/var/lib/proxnix/...`.

## Implementation Steps

1. Update `host/configuration.nix` so the wrapper imports from `/var/lib/proxnix/config/managed` instead of `/etc/nixos/managed`.
2. Update `host/base.nix` so guest runtime expectations point at `/var/lib/proxnix`, especially proxnix secret paths and any hash/runtime references.
3. Update `host/proxnix-secrets-guest` to use `/var/lib/proxnix/secrets` and `/var/lib/proxnix/runtime/...` paths instead of `/etc/proxnix/...` where appropriate.
4. Update `host/lxc/hooks/nixos-proxnix-mount` so all host-managed files except `/etc/nixos/configuration.nix` are mounted or copied into `/var/lib/proxnix/...`.
5. Move host-managed unit files to `/var/lib/proxnix/runtime/systemd-attached`.
6. Move host-managed attached scripts to `/var/lib/proxnix/runtime/bin`.
7. Decide how guest integration points for units and scripts are materialized after switch:
   - likely via activation-created symlinks or copied files from `/var/lib/proxnix/runtime/...`
   - not direct long-lived bind mounts into `/etc/systemd` or `/usr/local/bin`
8. Re-run the static-IP exercise and verify hashes, secrets, attached units, and status reporting after `nixos-rebuild switch`.

## Validation Checklist

- `/var/lib/proxnix/config/managed/...` exists after mount and after switch.
- `/var/lib/proxnix/runtime/current-config-hash` survives switch.
- `/var/lib/proxnix/runtime/applied-config-hash` is updated after successful switch.
- `/var/lib/proxnix/secrets/effective.sops.yaml` and `identity` remain available after switch.
- Attached units and scripts still function after switch.
- `/etc/nixos/configuration.nix` remains a wrapper importing from `/var/lib/proxnix/config/...`.
- Repeated `nixos-rebuild switch` runs do not fail because of the bootstrap `configuration.nix` bind mount.

## Decision Point

If NixOS does not tolerate self-managing `/etc/nixos/configuration.nix` while it begins life as a bind mount, then we need a different bootstrap mechanism.

That should be treated as a first-class architectural question, not papered over in the harness.
