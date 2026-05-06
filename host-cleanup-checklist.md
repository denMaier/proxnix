# Host-side Rust cleanup checklist

Slop accumulated during the bash → Rust port. Verified file:line refs marked ✓.

## High-impact

- [x] **#1 Duplicate helpers between `payload_stage.rs` and `common.rs`** ✓
  - `fn env_path` at `common.rs:59` and `payload_stage.rs:85`
  - `fn valid_vmid` at `common.rs:72` and `payload_stage.rs:387`
  - Action: delete the `payload_stage.rs` copies, import from `common`.

- [x] **#2 Status objects insert every field twice (camelCase + snake_case)** ✓
  - `reconcile_phase.rs:909/912` and ~5 other sites insert `desiredSystem` *and* `desired_system`, `currentSystem`/`current_system`, `hostActivatedSystem`/`host_activated_system`.
  - **Not pure slop**: `docs/reference/commands.md:351-353` documents camelCase as "compatibility fields"; `host/runtime/bin/proxnix-doctor:327-334` reads `desiredSystem` directly.
  - Done: status uses snake_case system fields only (`desired_system`, `current_system`, `previous_system`, `host_activated_system`, `guest_activated_system`). Removed camelCase writes, legacy camelCase reads, doctor fallback, docs, and test fixtures.

- [x] **#3 Six near-identical arg parsers in `reconcile_phase.rs`** ✓ — partial
  - `parse_reconcile_args` (l.280), `parse_rollback_args` (l.351), `parse_build_golden_args` (l.2602), `parse_seed_offline_args` (l.2631), `parse_seed_args` (l.2667), `parse_start_host_args` (l.2707).
  - On closer reading these aren't actually that similar — different flags, defaults, validation order, and `parse_seed_args` uniquely has passthrough behavior. A generic flag-table helper would add more lines than it removes. Six parsers for six subcommands is explicit dispatch, not slop.
  - What *was* repeated: 4× `env::var("X").map(|v| v == "1").unwrap_or(false)`. Extracted `env_bool(name)` to `common.rs`; converted all 4 sites in `reconcile_phase.rs` (parse_reconcile_args ×2, parse_rollback_args, start_container_for_offline_seed).

- [x] **#4 `find_in_path("pct"|"nix"|"socat")` repeated ~20 times** ✓
  - 13 `pct` lookups in `reconcile_phase.rs`, plus `gc.rs:258`, `flake_update.rs:243`.
  - Action: `require_pct() -> HostResult<PathBuf>` (and `_nix`, `_socat`) in `common.rs`.
  - Done: added `require_in_path(name) -> Result<PathBuf, String>` plus `require_pct`, `require_nix`, `require_nix_store`, and `require_socat` in `common.rs`; converted the `String`-error call sites in `reconcile_phase.rs`. Left the `io::Error` sites and intentional presence-check sites alone — different shapes.

## Medium-impact

- [x] **#5 `argv0_dispatch` (`main.rs:60`) handles exactly one symlink** ✓
  - Inline as a single match arm, or commit to argv0 dispatch and route the others.
  - Done: inlined as a 4-line argv0 check at the top of `run()`. The `nixos-proxnix-start-host` symlink is real and required (LXC config invokes the binary via that name).

- [x] **#6 `string_result` wrapper (`main.rs:56`) papers over a return-type split** ✓
  - Migrate `pve_conf::main`, `create_lxc::main`, `flake_update::main`, `gc::main` to `HostResult<()>`; delete the wrapper.
  - Done: 7 module entry points (`pve_conf::main`, `create_lxc::main`, `flake_update::main`, `gc::main`, `podman_secrets::main`, `template_bootstrap::main`, `authority::render_main`) converted to `HostResult<()>`. `?` propagation handles `Result<_, String>` via the existing `From<String>` for `HostError`. Direct `Err(...)` returns inside each `main` got `.into()`. `string_result` deleted.

- [x] **#7 Fail-build / locality-lost blocks copy-pasted in `reconcile_phase.rs`**
  - 3× `write_build_failed_status + eprintln + Err(...)`; 5× locality-loss blocks.
  - Action: extract `fail_build(config, ctx, reason)` and `check_locality_or_fail(...)`.
  - Done: extracted `fail_build(config, status, reason)` and `require_local_container(config, vmid, phase)`. `reconcile_build_phase` shrunk; 5 locality blocks (4 lines each → 1 line). Net ~-25 lines.

- [x] **#8 Two `from_env` parse the same env vars in `reconcile_phase.rs`**
  - `ReconcileConfig::from_env` (~l.242) and `BuildGoldenConfig::from_env` (~l.2581).
  - Action: shared `EnvPaths::from_env` for the common subset.
  - Done: extracted `ProxnixPaths` + `proxnix_paths_from_env()` (5 fields: root, authority, pve_lxc_dir, gcroot_dir, authority_render). Both configs destructure it and add their extras.

- [x] **#9 Two near-identical pve.conf parsers in `pve_conf.rs`**
  - `parse_pve_conf_content` (l.63) vs `parse_pve_conf_raw_content` (l.208).
  - Action: factor a single tokenizer.
  - Done: `parse_pve_conf_content` now calls `parse_pve_conf_raw_content` and only does the field projection. -10 lines.

- [~] **#10 Three almost-identical tree-copy functions in `reconcile_phase.rs`** — **considered, skipped**
  - `copy_tree_preserving_metadata`, `copy_file_preserving_metadata`, `push_tree_to_ct` (~l.1810–1953).
  - On reading, the three are not as similar as flagged. They share only the directory-walk scaffolding (~10 lines each); the per-entry action is genuinely different — host→host preserving uid/gid/mode vs host→container via `pct push` with a uniform file mode. `copy_file` is the leaf for `copy_tree`. A generic walker would need closures or a trait and would add more cognitive overhead at call sites than the duplication. Leaving as-is.

## Polish

- [x] **#11 Magic paths repeated** — `/etc/pve/lxc`, `/var/lib/proxnix/containers`, `var/lib/proxnix` as bare strings. Hoist to `const`s in `common.rs`.
  - Done: added `DEFAULT_PROXNIX_DIR`, `DEFAULT_PVE_LXC_DIR`, and `GUEST_PROXNIX_DIR` in `common.rs`; converted the Rust production call sites.

- [x] **#12 `pve_conf.rs` over-exports** — `nix_str`, `nix_str_list`, `parse_pve_conf_raw_content`, `parse_pve_tags` are `pub(crate)` with single callers. Tighten or move.
  - Done: made `nix_str_list`, `parse_pve_tags`, and `pve_conf_tags` private. Kept `nix_str`, `parse_pve_conf_raw`, `parse_pve_conf_raw_content`, and `pve_conf_has_tag` public within the crate because other modules use them.

- [x] **#13 Dead presence checks in `container_nix_daemon_connect_command`** (`reconcile_phase.rs:1268,1275`) — gates an error message on `find_in_path` for binaries that are only embedded as strings. Drop.
  - Done: the bridge now resolves `pct` once with `require_pct()` and passes the resolved path into the command string.

- [x] **#14 Unused `_nix` binding** at `reconcile_phase.rs:1203` — bound only to perform a presence check. Replace with a `require_nix()` call (after #4).
  - Done: replaced `_nix` / `_nix_store` bindings with direct `require_nix()?` and `require_nix_store()?` calls.

- [x] **#15 `unix = { package = "nix", ... }` rename in `Cargo.toml`** — verify the rename is needed and drop if not.
  - Done: verified the alias is intentional. Kept `unix = { package = "nix", ... }` so OS-level crate calls are visually distinct from Nix package-manager operations.

- [x] **#16 Inconsistent error-wrap style** — mix of `.map_err(|_| format!(...))` (drops cause) and `.map_err(|err| format!("...: {err}"))` (preserves). Sweep to "always preserve".
  - Done: removed the remaining `map_err(|_| ...)` sites in `crates/proxnix-host/src`, preserving the underlying error in status reads and lock acquisition failures.
