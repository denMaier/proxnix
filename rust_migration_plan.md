# Rust Migration Plan

## Target Shape

The host-side control plane should converge toward:

- [x] Minimal Proxmox/LXC hook scripts that adapt hook invocation into `proxnix-host`.
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
- [x] Ported authority rendering into `proxnix-host authority render`.
- [x] Replaced `proxnix-authority-render` with a thin Rust dispatch wrapper.
- [x] Removed the Python `proxnix_authority_render.py` implementation.
- [x] Ported LXC prestart, mount, and poststop hook internals into
  `proxnix-host hook`.
- [x] Replaced LXC hook scripts with thin dispatch wrappers.
- [x] Ported `proxnix-gc` into `proxnix-host gc`.
- [x] Replaced `proxnix-gc` with a thin Rust dispatch wrapper.
- [x] Ported `proxnix-flake-update` into `proxnix-host flake-update`.
- [x] Replaced `proxnix-flake-update` with a thin Rust dispatch wrapper.
- [x] Ported golden-template build warming into
  `proxnix-host reconcile build-golden`.
- [x] Replaced `proxnix-reconcile-build-golden` with a thin Rust dispatch wrapper.
- [x] Ported stopped-rootfs seeding into `proxnix-host reconcile seed-offline`.
- [x] Replaced `proxnix-reconcile-seed-offline` with a thin Rust dispatch
  wrapper.
- [x] Ported seed dispatch into `proxnix-host reconcile seed`.
- [x] Replaced `proxnix-reconcile-seed` with a thin Rust dispatch wrapper.
- [x] Ported build and activate phase dispatch into `proxnix-host reconcile build`
  and `proxnix-host reconcile activate`.
- [x] Replaced `proxnix-reconcile-build` and `proxnix-reconcile-activate` with
  thin Rust dispatch wrappers.
- [x] Removed the unused `nixos-proxnix-common.sh` hook compatibility helper.

## Current Status

As of 2026-04-30, the latest completed migration checkpoint is `Remove unused
hook common helper`. The last three committed migration steps are:

- `ab19fda Remove unused hook common helper`
- `5a261ec Port phase dispatch wrappers to Rust`
- `7c3f269 Port seed dispatch to Rust`

The next recommended checkpoint is `Split Rust controller modules`: move
already-ported hook, authority, reconciler-state, and Podman-secrets code out
of `host/rust/src/main.rs` before porting another large shell surface. This
keeps `proxnix-host` as one binary without concentrating all implementation
detail in one huge `main.rs`.

The current migration batch includes:

- Rust hook handling in `host/rust/src/main.rs`, including
  `proxnix-host hook prestart`, `proxnix-host hook mount`, and
  `proxnix-host hook poststop`.
- Rust GC handling in `host/rust/src/gc.rs`, including `proxnix-host gc` and
  replacement tests for stale stage and GC-root pruning.
- Rust flake update handling in `host/rust/src/flake_update.rs`, including
  `proxnix-host flake-update` and replacement tests for frequency gating, input
  forwarding, lock persistence, and root lock propagation.
- Rust phase-command handling in `host/rust/src/reconcile_phase.rs`, including
  `proxnix-host reconcile build`, `proxnix-host reconcile build-golden`,
  `proxnix-host reconcile seed`, `proxnix-host reconcile seed-offline`, and
  `proxnix-host reconcile activate`; replacement tests cover golden template
  builds, published lock preservation, stopped-container rejection for running
  seed dispatch, stopped-rootfs profile repair, runtime markers, and guest
  activation marker observation.
- `host/runtime/lxc/hooks/nixos-proxnix-prestart`,
  `host/runtime/lxc/hooks/nixos-proxnix-mount`, and
  `host/runtime/lxc/hooks/nixos-proxnix-poststop` as thin dispatch wrappers.
- `host/runtime/bin/proxnix-gc` and `host/runtime/bin/proxnix-flake-update` as
  thin dispatch wrappers.
- `host/runtime/bin/proxnix-reconcile-build`,
  `host/runtime/bin/proxnix-reconcile-build-golden`,
  `host/runtime/bin/proxnix-reconcile-seed`,
  `host/runtime/bin/proxnix-reconcile-seed-offline`, and
  `host/runtime/bin/proxnix-reconcile-activate` as thin dispatch wrappers.
- `host/runtime/lxc/hooks/nixos-proxnix-common.sh` removed after all hook logic
  moved into `proxnix-host hook`.
- Replacement Rust tests for LXC hook argument parsing, post-stop stage cleanup,
  relay identity payload parsing, and Proxmox host-root UID detection.
- Host install and docs updates so shipped LXC hook paths describe and assert
  the Rust hook controller surface.
- Previous authority rendering migration remains in the batch history:
  `proxnix-host authority render`, the thin `proxnix-authority-render` wrapper,
  and deletion of the Python authority renderer.
- Broader in-progress host runtime changes around closure seeding, gcroots,
  `nix copy`, offline seed profile repair, and flake update.
- Flake update files:
  `host/runtime/bin/proxnix-flake-update`,
  `host/runtime/systemd/proxnix-flake-update.service`,
  `host/runtime/systemd/proxnix-flake-update.timer`, and
  `host/tests/test_flake_update.py`.
- Workstation publish CLI/test edits that preserve a host-managed remote
  `flake.lock` when the local tree omits one.

Verification run for the current migration batch:

- `nix shell nixpkgs#cargo nixpkgs#rustc nixpkgs#rustfmt nixpkgs#clang -c cargo test`
  passed with 31 Rust tests.
- `python -m unittest discover host/tests` passed with 29 host tests.
- `PYTHONPATH=workstation/cli/src python -m unittest discover workstation/cli/tests`
  passed with 89 workstation tests.
- `nix build --no-link --print-out-paths .#proxnix-host-rust` passed.
- `nix eval .#packages.x86_64-linux.proxnix-host.name` passed and returned
  `"proxnix-host-0.6.1"`.
- `nix build --no-link --print-out-paths .#packages.x86_64-linux.proxnix-host`
  was not run to completion on the local `aarch64-darwin` builder because it
  requires an `x86_64-linux` build system.
- `bash -n` passed for the edited host runtime shell scripts, hook, and
  uninstall script.
- `git diff --check` passed.

Generated verification artifacts were removed after the run:

- `host/rust/target/`
- temporary generated `flake.lock`

## Current Host Runtime To Collapse

### Hook Layer

- [x] `host/runtime/lxc/hooks/nixos-proxnix-prestart`
- [x] `host/runtime/lxc/hooks/nixos-proxnix-mount`
- [x] `host/runtime/lxc/hooks/nixos-proxnix-poststop`
- [x] `host/runtime/lxc/hooks/nixos-proxnix-common.sh`

Target: keep tiny shell entrypoints only where LXC requires shell/script files, and move logic into:

- [x] `proxnix-host hook prestart`
- [x] `proxnix-host hook mount`
- [x] `proxnix-host hook poststop`

### Controller Commands

- [ ] `proxnix-reconcile`
- [x] `proxnix-reconcile-build-golden`
- [x] `proxnix-reconcile-build`
- [x] `proxnix-reconcile-seed`
- [x] `proxnix-reconcile-seed-offline`
- [x] `proxnix-reconcile-activate`
- [ ] `proxnix-create-lxc`
- [x] `proxnix-gc`
- [x] `proxnix-flake-update`
- [x] `proxnix-authority-render`
- [x] `proxnix-reconciler-state`

Target: make these subcommands of `proxnix-host`, then decide whether old command names stay as symlinks/wrappers:

- [ ] `proxnix-host reconcile`
- [x] `proxnix-host reconcile build-golden`
- [x] `proxnix-host reconcile build`
- [x] `proxnix-host reconcile seed`
- [x] `proxnix-host reconcile seed-offline`
- [x] `proxnix-host reconcile activate`
- [ ] `proxnix-host create-lxc`
- [x] `proxnix-host gc`
- [x] `proxnix-host flake-update`
- [x] `proxnix-host authority render`
- [x] `proxnix-host state`

### Python Libraries

- [x] `host/runtime/lib/proxnix_authority_render.py`
- [x] `host/runtime/lib/proxnix_reconciler_state.py`
- [x] `host/runtime/lib/proxnix_reconcile_podman_secrets.py`

Target:

- [x] Port authority rendering to Rust and delete `proxnix_authority_render.py`.
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
5. [x] Port authority rendering.
6. [x] Move hook internals into Rust subcommands and leave thin hook entrypoints.
7. [x] Port GC and flake-update.
8. [x] Port seed/build/activate helpers.
9. [ ] Split `host/rust/src/main.rs` into focused modules before adding more
   large controller surfaces.
10. [ ] Port `proxnix-create-lxc` into `proxnix-host create-lxc`, or decide it
    should remain a standalone shell helper temporarily.
11. [ ] Port main reconcile orchestration.
12. [ ] Port or intentionally preserve doctor.
13. [ ] Replace compatibility wrappers with symlinks or remove them where callers can use `proxnix-host`.
14. [ ] Update docs to describe the final host-side shape.

## Verification Gates

- [x] Rust unit tests cover pure parsing/rendering/state transformations.
- [x] Existing Python/shell tests are retired only when equivalent Rust tests exist.
- [x] `nix build .#proxnix-host-rust` succeeds locally.
- [x] `nix eval .#packages.x86_64-linux.proxnix-host.name` succeeds for Linux package shape.
- [x] Host install tests assert only the intended runtime files are shipped.
- [x] Hook tests or harnesses exercise the same entrypoints used by Proxmox.
- [x] Reconcile tests prove locality checks still run immediately before mutating CTs.

## Open Decisions

- [ ] Whether `proxnix-doctor` remains intentionally shell-based.
- [ ] Whether old command names become symlinks to `proxnix-host` or tiny wrappers with command-specific defaults.
- [ ] Whether Rust should use external crates for SQLite/JSON/CLI parsing now, or keep the early crate minimal until orchestration is ported.
- [ ] Whether `proxnix-secrets-guest` is kept as guest-side shell or eventually moved into a separate guest helper binary.
