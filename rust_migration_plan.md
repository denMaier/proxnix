# Rust Migration Plan

## Target Shape

The host-side control plane should converge toward:

- [ ] Minimal Proxmox/LXC hook scripts that adapt hook invocation into `proxnix-host`.
- [ ] One Rust controller binary, `proxnix-host`, for host orchestration.
- [ ] A doctor surface, either as the current `proxnix-doctor` shell script or as `proxnix-host doctor`.

Keep these as non-Rust configuration and packaging surfaces:

- [ ] NixOS guest modules under `host/runtime/nix/`.
- [ ] LXC config snippets under `host/runtime/lxc/config/`.
- [ ] systemd unit files under `host/runtime/systemd/`.
- [ ] Ansible install/deploy playbooks under `host/deploy/`.
- [ ] Guest-staged helper behavior that truly runs inside the guest, such as `proxnix-secrets-guest`, until it has a separate migration decision.

## Rules

- [x] Do not keep duplicate implementations for migrated behavior.
- [x] Keep compatibility command names only as thin dispatch surfaces when needed.
- [x] Commit in small checkpoints.
- [x] Port behavior behind tests before deleting established shell/Python behavior.
- [ ] Keep Proxmox, Nix, SOPS, age, rsync, and systemd as explicit external tools where they are the right authority.

## Done

- [x] Added Rust crate at `host/rust`.
- [x] Added `proxnix-host` controller binary.
- [x] Added Nix package `.#proxnix-host-rust`.
- [x] Packaged `proxnix-host` into the existing `.#proxnix-host` host profile.
- [x] Exposed `proxnix-host` through host activation.
- [x] Made `proxnix-host --version` report the host package version.
- [x] Ported `pve-conf-to-nix.py` behavior into `proxnix-host pve-conf-to-nix`.
- [x] Removed the Python `pve-conf-to-nix.py` implementation.
- [x] Removed the temporary `pve-conf-to-nix.py` compatibility wrapper.
- [x] Updated the pre-start hook to call `proxnix-host pve-conf-to-nix` directly.
- [x] Ported Podman secrets reconciliation into `proxnix-host reconcile podman-secrets`.
- [x] Updated the mount hook to call the Rust Podman secrets reconciler directly.
- [x] Removed the Python `proxnix_reconcile_podman_secrets.py` implementation.
- [x] Ported reconciler SQLite state into `proxnix-host state`.
- [x] Replaced `proxnix-reconciler-state` with a thin Rust dispatch wrapper.
- [x] Removed the Python `proxnix_reconciler_state.py` implementation.

## Current Host Runtime To Collapse

### Hook Layer

- [ ] `host/runtime/lxc/hooks/nixos-proxnix-prestart`
- [ ] `host/runtime/lxc/hooks/nixos-proxnix-mount`
- [ ] `host/runtime/lxc/hooks/nixos-proxnix-poststop`
- [ ] `host/runtime/lxc/hooks/nixos-proxnix-common.sh`

Target: keep tiny shell entrypoints only where LXC requires shell/script files, and move logic into:

- [ ] `proxnix-host hook prestart`
- [ ] `proxnix-host hook mount`
- [ ] `proxnix-host hook poststop`

### Controller Commands

- [ ] `proxnix-reconcile`
- [ ] `proxnix-reconcile-build-golden`
- [ ] `proxnix-reconcile-build`
- [ ] `proxnix-reconcile-seed`
- [ ] `proxnix-reconcile-seed-offline`
- [ ] `proxnix-reconcile-activate`
- [ ] `proxnix-create-lxc`
- [ ] `proxnix-gc`
- [ ] `proxnix-flake-update`
- [ ] `proxnix-authority-render`
- [x] `proxnix-reconciler-state`

Target: make these subcommands of `proxnix-host`, then decide whether old command names stay as symlinks/wrappers:

- [ ] `proxnix-host reconcile`
- [ ] `proxnix-host reconcile build-golden`
- [ ] `proxnix-host reconcile build`
- [ ] `proxnix-host reconcile seed`
- [ ] `proxnix-host reconcile seed-offline`
- [ ] `proxnix-host reconcile activate`
- [ ] `proxnix-host create-lxc`
- [ ] `proxnix-host gc`
- [ ] `proxnix-host flake-update`
- [ ] `proxnix-host authority render`
- [x] `proxnix-host state`

### Python Libraries

- [ ] `host/runtime/lib/proxnix_authority_render.py`
- [x] `host/runtime/lib/proxnix_reconciler_state.py`
- [x] `host/runtime/lib/proxnix_reconcile_podman_secrets.py`

Target:

- [ ] Port authority rendering to Rust and delete `proxnix_authority_render.py`.
- [x] Port reconciler SQLite state to Rust and delete `proxnix_reconciler_state.py`.
- [x] Port Podman `secrets.json` reconciliation to Rust and delete `proxnix_reconcile_podman_secrets.py`.

### Doctor

- [ ] Keep `proxnix-doctor` as shell temporarily.
- [ ] Decide whether emergency diagnosability is better served by keeping it shell.
- [ ] If not, port to `proxnix-host doctor` and keep `proxnix-doctor` only as a compatibility dispatch name.

### Install/Activation

- [ ] Keep `proxnix-host-activate` as shell while package layout is changing.
- [ ] Keep `proxnix-host-uninstall` as shell while cleanup semantics are still broad.
- [ ] Revisit after controller commands and hooks are mostly migrated.

## Migration Order

1. [x] Establish Rust binary and packaging.
2. [x] Port a pure helper and delete the old implementation.
3. [x] Port Podman secrets reconciliation, because it is bounded and file-oriented.
4. [x] Port reconciler state, including SQLite schema and CLI.
5. [ ] Port authority rendering.
6. [ ] Move hook internals into Rust subcommands and leave thin hook entrypoints.
7. [ ] Port GC and flake-update.
8. [ ] Port seed/build/activate helpers.
9. [ ] Port main reconcile orchestration.
10. [ ] Port or intentionally preserve doctor.
11. [ ] Replace compatibility wrappers with symlinks or remove them where callers can use `proxnix-host`.
12. [ ] Update docs to describe the final host-side shape.

## Verification Gates

- [x] Rust unit tests cover pure parsing/rendering/state transformations.
- [x] Existing Python/shell tests are retired only when equivalent Rust tests exist.
- [x] `nix build .#proxnix-host-rust` succeeds locally.
- [ ] `nix eval .#packages.x86_64-linux.proxnix-host.name` succeeds for Linux package shape.
- [ ] Host install tests assert only the intended runtime files are shipped.
- [ ] Hook tests or harnesses exercise the same entrypoints used by Proxmox.
- [ ] Reconcile tests prove locality checks still run immediately before mutating CTs.

## Open Decisions

- [ ] Whether `proxnix-doctor` remains intentionally shell-based.
- [ ] Whether old command names become symlinks to `proxnix-host` or tiny wrappers with command-specific defaults.
- [ ] Whether Rust should use external crates for SQLite/JSON/CLI parsing now, or keep the early crate minimal until orchestration is ported.
- [ ] Whether `proxnix-secrets-guest` is kept as guest-side shell or eventually moved into a separate guest helper binary.
