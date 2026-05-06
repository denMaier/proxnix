# Rust Migration Plan

## Target Shape

The host-side control plane should converge toward:

- [x] Minimal Proxmox/LXC hook scripts that adapt hook invocation into `proxnix-host`.
- [x] One Rust controller binary, `proxnix-host`, for host orchestration.
- [x] A doctor surface, intentionally kept as the current `proxnix-doctor`
  shell script.

Keep these as non-Rust configuration and packaging surfaces:

- [x] NixOS guest modules under `host/runtime/nix/`.
- [x] LXC config snippets under `host/runtime/lxc/config/`.
- [x] systemd unit files under `host/runtime/systemd/`.
- [x] Ansible install/deploy playbooks under `host/deploy/`.
- [x] Guest-staged helper behavior that truly runs inside the guest, such as
  `proxnix-secrets-guest`, until it has a separate migration decision.

## Rules

- [x] Do not keep duplicate implementations for migrated behavior.
- [x] Keep compatibility command names only as thin dispatch surfaces when needed.
- [x] Commit in small checkpoints.
- [x] Port behavior behind tests before deleting established shell/Python behavior.
- [x] Keep Proxmox, Nix, SOPS, age, and systemd as explicit external tools where they are the right authority.

## Done

- [x] Added Rust crate at `crates/proxnix-host`.
- [x] Added `proxnix-host` controller binary.
- [x] Added Nix package `.#proxnix-host-controller` and removed the old `.#proxnix-host-rust` package alias.
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
- [x] Removed the `proxnix-reconciler-state` compatibility command surface.
- [x] Removed the Python `proxnix_reconciler_state.py` implementation.
- [x] Ported authority rendering into `proxnix-host authority render`.
- [x] Removed the `proxnix-authority-render` compatibility command surface.
- [x] Removed the Python `proxnix_authority_render.py` implementation.
- [x] Ported LXC prestart, mount, and poststop hook internals into
  `proxnix-host hook`.
- [x] Replaced LXC hook scripts with package symlinks to `proxnix-host`.
- [x] Ported `proxnix-gc` into `proxnix-host gc`.
- [x] Removed the `proxnix-gc` compatibility command surface.
- [x] Ported `proxnix-flake-update` into `proxnix-host flake-update`.
- [x] Removed the `proxnix-flake-update` compatibility command surface.
- [x] Ported golden-template build warming into
  `proxnix-host reconcile build-golden`.
- [x] Removed the `proxnix-reconcile-build-golden` compatibility command surface.
- [x] Ported stopped-rootfs seeding into `proxnix-host reconcile seed-offline`.
- [x] Removed the `proxnix-reconcile-seed-offline` compatibility command surface.
- [x] Ported seed dispatch into `proxnix-host reconcile seed`.
- [x] Removed the `proxnix-reconcile-seed` compatibility command surface.
- [x] Ported build and activate phase dispatch into `proxnix-host reconcile build`
  and `proxnix-host reconcile activate`.
- [x] Removed the `proxnix-reconcile-build` and `proxnix-reconcile-activate`
  compatibility command surfaces.
- [x] Removed the unused `nixos-proxnix-common.sh` hook compatibility helper.
- [x] Split `proxnix-host` Rust controller implementation out of
  `crates/proxnix-host/src/main.rs` into focused modules for common helpers, hooks, PVE
  config rendering, authority rendering, reconciler state, and Podman secrets.
- [x] Ported `proxnix-create-lxc` into `proxnix-host create-lxc`.
- [x] Ported main reconcile orchestration into `proxnix-host reconcile`.
- [x] Decided to preserve `proxnix-doctor` as shell for emergency host
  diagnosability.
- [x] Removed migrated host command wrappers; systemd and operators use
  `proxnix-host ...` subcommands directly.

## Current Status

As of 2026-04-30, the latest completed migration checkpoint in the working tree
is `Collapse host compatibility wrappers into proxnix-host`. The last three committed
migration steps remain:

- `ab19fda Remove unused hook common helper`
- `5a261ec Port phase dispatch wrappers to Rust`
- `7c3f269 Port seed dispatch to Rust`

The Rust migration plan is complete except for future non-controller decisions
around guest-side helpers and broad install/uninstall shell surfaces.

The current migration batch includes:

- Rust hook handling in `crates/proxnix-host/src/hook.rs`, including
  `proxnix-host hook prestart`, `proxnix-host hook mount`, and
  `proxnix-host hook poststop`.
- Rust GC handling in `crates/proxnix-host/src/gc.rs`, including `proxnix-host gc` and
  replacement tests for stale stage and GC-root pruning.
- Rust flake update handling in `crates/proxnix-host/src/flake_update.rs`, including
  `proxnix-host flake-update` and replacement tests for frequency gating, input
  forwarding, lock persistence, and root lock propagation.
- Rust phase-command handling in `crates/proxnix-host/src/reconcile_phase.rs`, including
  `proxnix-host reconcile`, `proxnix-host reconcile build`,
  `proxnix-host reconcile build-golden`, `proxnix-host reconcile seed`,
  `proxnix-host reconcile seed-offline`, and `proxnix-host reconcile activate`;
  replacement tests cover dry-run planning, cluster locality checks, build-only
  status, running-CT seeding, activation, rollback, recreate-missing
  orchestration, golden template builds, published lock preservation,
  stopped-container rejection for running seed dispatch, stopped-rootfs profile
  repair, runtime markers, and guest activation marker observation.
- Rust controller modules split across `crates/proxnix-host/src/common.rs`,
  `crates/proxnix-host/src/hook.rs`, `crates/proxnix-host/src/pve_conf.rs`,
  `crates/proxnix-host/src/authority.rs`, `crates/proxnix-host/src/state.rs`, and
  `crates/proxnix-host/src/podman_secrets.rs`, with `crates/proxnix-host/src/main.rs` reduced to
  command routing and shared test fixtures.
- Rust LXC creation handling in `crates/proxnix-host/src/create_lxc.rs`, including
  `proxnix-host create-lxc`; replacement tests cover argument parsing, `pct
  create` command construction, template volid normalization, validation, and
  dry-run shell quoting.
- `host/runtime/bin/proxnix-doctor` intentionally remains shell. It is a
  best-effort emergency diagnostic surface for Proxmox, systemd, Nix, installed
  host files, and running guest checks, so it stays readable and runnable with
  only baseline host tools.
- The Nix host package now installs `proxnix-host` as the only Rust host
  command surface. Systemd units call `proxnix-host ...` directly.
- `host/runtime/lxc/hooks/nixos-proxnix-prestart`,
  `host/runtime/lxc/hooks/nixos-proxnix-mount`, and
  `host/runtime/lxc/hooks/nixos-proxnix-poststop` are installed as symlinks to
  `proxnix-host`; `argv[0]` dispatch remains only for these LXC hook names.
- `host/runtime/lxc/hooks/nixos-proxnix-common.sh` removed after all hook logic
  moved into `proxnix-host hook`.
- Replacement Rust tests for LXC hook argument parsing, post-stop stage cleanup,
  relay identity payload parsing, and Proxmox host-root UID detection.
- Host install and docs updates so shipped LXC hook paths describe and assert
  the Rust hook controller surface.
- Previous authority rendering migration remains in the batch history:
  `proxnix-host authority render` and deletion of the Python authority renderer.
- Broader in-progress host runtime changes around closure seeding, gcroots,
  `nix copy`, offline seed profile repair, and flake update.
- Flake update files:
  `host/runtime/systemd/proxnix-flake-update.service`,
  `host/runtime/systemd/proxnix-flake-update.timer`, and
  `host/tests/test_flake_update.py`.
- Workstation publish CLI/test edits that preserve a host-managed remote
  `flake.lock` when the local tree omits one.

Verification run for the current migration batch:

- `nix shell nixpkgs#cargo nixpkgs#rustc nixpkgs#rustfmt nixpkgs#clang -c cargo test`
  passed with 37 Rust tests.
- `nix shell nixpkgs#cargo nixpkgs#rustc nixpkgs#rustfmt nixpkgs#clang -c cargo build`
  passed and produced a local debug `proxnix-host` used by the wrapper-level
  host tests.
- `python -m unittest discover host/tests` passed with 29 host tests.
- `PYTHONPATH=workstation/cli/src python -m unittest discover workstation/cli/tests`
  passed with 89 workstation tests.
- `nix build --no-link --print-out-paths .#proxnix-host-controller` passed.
- `nix eval .#packages.x86_64-linux.proxnix-host.name` passed and returned
  `"proxnix-host-0.6.1"`.
- `nix build --no-link --print-out-paths .#packages.x86_64-linux.proxnix-host`
  was not run to completion on the local `aarch64-darwin` builder because it
  requires an `x86_64-linux` build system.
- `bash -n` passed for the edited host runtime shell scripts, hook, and
  uninstall script.
- `git diff --check` passed.

Generated verification artifacts were removed after the run:

- `target/`
- temporary generated `flake.lock`

## Current Host Runtime To Collapse

### Hook Layer

- [x] `host/runtime/lxc/hooks/nixos-proxnix-prestart` packaged as a symlink
- [x] `host/runtime/lxc/hooks/nixos-proxnix-mount` packaged as a symlink
- [x] `host/runtime/lxc/hooks/nixos-proxnix-poststop` packaged as a symlink
- [x] `host/runtime/lxc/hooks/nixos-proxnix-common.sh`

Target: avoid shell hook entrypoints; install hook names as symlinks to `proxnix-host` and move logic into:

- [x] `proxnix-host hook prestart`
- [x] `proxnix-host hook mount`
- [x] `proxnix-host hook poststop`

### Controller Commands

- [x] `proxnix-host reconcile`
- [x] `proxnix-host reconcile build-golden`
- [x] `proxnix-host reconcile build`
- [x] `proxnix-host reconcile seed`
- [x] `proxnix-host reconcile seed-offline`
- [x] `proxnix-host reconcile activate`
- [x] `proxnix-host create-lxc`
- [x] `proxnix-host gc`
- [x] `proxnix-host flake-update`
- [x] `proxnix-host authority render`
- [x] `proxnix-host state`

Target: make these subcommands of `proxnix-host` and remove old command names.

- [x] `proxnix-host reconcile`
- [x] `proxnix-host reconcile build-golden`
- [x] `proxnix-host reconcile build`
- [x] `proxnix-host reconcile seed`
- [x] `proxnix-host reconcile seed-offline`
- [x] `proxnix-host reconcile activate`
- [x] `proxnix-host create-lxc`
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

- [x] Keep `proxnix-doctor` as shell intentionally.
- [x] Decide that emergency diagnosability is better served by keeping it shell.
- [x] Do not port to `proxnix-host doctor` in this migration.

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
9. [x] Split `crates/proxnix-host/src/main.rs` into focused modules before adding more
   large controller surfaces.
10. [x] Port `proxnix-create-lxc` into `proxnix-host create-lxc`, or decide it
    should remain a standalone shell helper temporarily.
11. [x] Port main reconcile orchestration.
12. [x] Port or intentionally preserve doctor.
13. [x] Remove host compatibility wrappers; keep only `proxnix-host` plus LXC hook-name symlinks.
14. [x] Update docs to describe the final host-side shape.

## Verification Gates

- [x] Rust unit tests cover pure parsing/rendering/state transformations.
- [x] Existing Python/shell tests are retired only when equivalent Rust tests exist.
- [x] `nix build .#proxnix-host-controller` succeeds locally.
- [x] `nix eval .#packages.x86_64-linux.proxnix-host.name` succeeds for Linux package shape.
- [x] Host install tests assert only the intended runtime files are shipped.
- [x] Hook tests or harnesses exercise the same entrypoints used by Proxmox.
- [x] Reconcile tests prove locality checks still run immediately before mutating CTs.

## Open Decisions

- [x] Whether `proxnix-doctor` remains intentionally shell-based: yes, keep it
  shell for emergency diagnosability and low-dependency host checks.
- [x] Whether old command names become symlinks to `proxnix-host` or tiny
  wrappers with command-specific defaults: migrated host commands are package
  symlinks to `proxnix-host` using `argv[0]` dispatch; source-tree wrappers
  remain as development compatibility surfaces.
- [x] Whether Rust should use external crates for SQLite/JSON/CLI parsing now:
  use focused crates where they remove real risk (`rusqlite`, `serde_json`,
  `uuid`) while keeping CLI parsing manual for the current small command
  surface.
- [ ] Whether `proxnix-secrets-guest` is kept as guest-side shell or eventually moved into a separate guest helper binary.
