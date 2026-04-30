# Host-Side Review Fix Plan

Tracker for the fixes identified in the host-side code review (branch
`host-nix-reconciler-pivot`). Each item is checked off as it lands. Phases are
intended to be committed independently.

Clarification from operator: the per-VMID **stage directory** (under
`/var/lib/proxnix/containers/<vmid>/stage`) is host-side scratch space used to
*assemble* the closure to be copied **into** the container at boot. Once the
container is up and the copy is complete, the stage dir is no longer needed.
Therefore `proxnix-gc` reaping stage dirs of running containers is **correct by
design**; only the misleading log wording needs fixing.

---

## Phase 1 — Make Nix gcroots real (Critical)

Addresses findings #1, #2, #15, #16.

- [x] `protect_host_closure` uses `nix-store --add-root --indirect`
      (`host/runtime/bin/proxnix-reconcile`)
- [x] `protect_golden_closure` uses `nix-store --add-root --indirect`
      (`host/runtime/bin/proxnix-reconcile-build-golden`)
- [x] `proxnix-host state` reports truthful gcroot status
      (verified via `nix-store --query --roots`, not hardcoded)
- [x] Test in `host/tests/` asserts `nix-store --query --roots <closure>` lists
      the gcroot path
- [x] `host/install/uninstall.sh` removes indirect roots so subsequent host GC
      can reclaim space
- [x] Ansible install drops a host nix.conf snippet
      (`experimental-features = nix-command flakes` only — indirect gcroots
      already pin the closures we need; we deliberately do not enable
      `keep-outputs` / `keep-derivations` system-wide)
- [x] Operator runbook entry: do not run `nix-collect-garbage` against the host
      store; use `proxnix-gc` instead

**Validation:** on a scratch Proxmox node, build a closure → run
`nix-collect-garbage -d` → confirm closure survives.

---

## Phase 2 — Hook robustness (High)

Addresses findings #3, #11, log-wording portion of #6.

- [x] `nixos-proxnix-prestart` switched to `set -euo pipefail` with explicit
      error handling for legitimately optional ops
- [x] `nixos-proxnix-mount` switched to `set -euo pipefail`
- [x] `nixos-proxnix-poststop` switched to `set -euo pipefail`
- [x] `finish_prestart` trap reads stage state variable rather than `$?`
      so partial-stage failures always trigger `cleanup_stage`
- [x] `validate_vmid()` helper added to `nixos-proxnix-common.sh` and used by
      all three hooks
- [x] `proxnix-gc` log wording: "released stage dir for booted CT (content
      already copied into guest)" + comment explaining design contract
- [ ] Shellcheck clean on `host/runtime/bin/*` and `host/runtime/lxc/hooks/*`
      (not run locally; `shellcheck` was not installed)

---

## Phase 3 — Secrets & guest-trust hygiene (High / Security)

Addresses findings #7, #8, #10.

- [x] Identity decryption no longer lands plaintext in `/tmp` (memfd / stdin
      pipe / tmpfs with shred trap)
- [x] Podman `secrets.json` reconciliation atomic (write-tmp + `os.replace`)
- [x] Reconciler records activated-system on the **host** rather than trusting
      a guest-written marker; guest marker may be cross-checked for drift but
      is not authoritative

---

## Phase 4 — Reconciler internals (High / Medium)

Addresses findings #4, #5, #9, #12.

- [x] `seed_closure` callers no longer use `if !` errexit-suppression pattern;
      explicit `rc=$?` propagation
- [x] `nix-store --query --requisites … | xargs …` switched to NUL-delimited
      with `xargs -0 -r`; defensive non-empty assertion on requisites
- [x] Auto-start of stopped CTs gated behind `--start-stopped` flag (or env)
- [x] `proxnix-reconcile main()`: rename local `container` → `container_id`
- [x] `proxnix-create-lxc`: rename `HOSTNAME` → `CT_HOSTNAME`

---

## Phase 5 — Cleanup (Low)

Addresses findings #13, #14, #17.

- [x] `proxnix-doctor` required-files list reflected the state helper while it
      was Python-based; the helper has since moved behind `proxnix-host state`
- [x] `proxnix-host-activate` drops the `systemctl disable --now
      proxnix-reconcile.timer` line (timer not installed in this version)
- [x] `host/nix/proxnix-host.nix` drops the redundant `cp` of
      `proxnix-host-activate`

---

## Validation matrix

| Phase | Unit / static | Manual on Proxmox |
|-------|---------------|-------------------|
| 1     | `host/tests/test_reconcile.py` extended | Build → `nix-collect-garbage -d` → closure survives |
| 2     | `shellcheck` clean | Inject failure mid-prestart → cleanup_stage runs, `pct start` fails loudly |
| 3     | `inotifywait /tmp` clean during reconcile; secrets unit test | Kill mount hook mid-write → `secrets.json` not truncated; tamper guest marker → host status correct |
| 4     | `seed_closure` failure unit test | `proxnix-reconcile <stopped vmid>` errors actionably; `--start-stopped` works |
| 5     | n/a | `proxnix-doctor` passes; activate idempotent |
