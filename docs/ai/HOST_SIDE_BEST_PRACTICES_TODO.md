# Host-Side Best Practices TODO

Tracker for the host-side deployment review from 2026-05-06. Items are ordered
by operational risk, with decision points called out explicitly.

## Correctness and Transactionality

- [x] Parse Proxmox container configuration with section awareness so `[pending]`
      and snapshot sections are not treated as live state.
- [x] Fix recreate-missing manifest-to-CLI normalization so booleans such as
      `pve.unprivileged = true` become `1`/`0` for `proxnix-host create-lxc`.
- [x] Make guest payload sync staged and atomic instead of deleting live guest
      runtime/secrets paths before replacement.
- [x] Make generated authority rendering use a fresh tree or explicitly remove
      stale generated files whose sources disappeared.
- [x] Make `proxnix-gc` coordinate with the global reconcile/staging lock before
      pruning stage directories.
- [x] Materialize generated guest secret/config files in destination directories
      before rename so writes remain atomic across filesystem boundaries.

## Supply Chain and Install Safety

- [x] Change Ansible host-profile install flow so clean-slate is an explicit
      repair/reset path, not the default activation path.
- [x] Keep the floating development flake ref for now because deployments are
      development-only until the path is proven reliable enough for releases.
- [x] Require preinstalled Nix by default and keep Determinate installation as
      an explicit convenience bootstrap option.

## Structured Data and Validation

- [x] Replace the SOPS/YAML runtime secret chain with a single self-describing
      encrypted secret bundle. Do not use a separate manifest/index file because
      that creates split-brain risk. This must be done as one cross-component
      contract across workstation publishing, host reconcile/Podman registration,
      and the guest secret helper.
- [x] Keep encrypted-at-rest guest delivery as the Option A contract:
      `effective.secrets.json` remains encrypted for the guest, and the
      host-relay-encrypted `age_identity.age` is decrypted only into the staged
      guest secret identity file.
- [x] Use `effective.secrets.json` as the published runtime bundle contract with
      schema `proxnix.secrets.v1` and per-secret armored age ciphertext.
- [x] Make the workstation, host, and guest consume the same bundle shape:
      workstation publishes it, host stages and registers Podman names from it,
      and the guest helper decrypts it directly.
- [x] Ship a narrow `proxnix-secrets-guest` binary into containers instead of
      copying the full host controller binary.
- [x] Move visible workstation defaults from `embedded-sops` and
      `PROXNIX_SOPS_MASTER_IDENTITY` to `embedded-age` and
      `PROXNIX_AGE_MASTER_IDENTITY`, while keeping legacy names as read-only
      compatibility fallbacks.
- [x] Add focused regression tests around Proxmox config parsing and
      recreate-missing manifest normalization.
- [x] Add focused regression tests around generated authority cleanup.

## Repository Boundaries

- [x] Move real site-specific host inventory and extras into the site repo, and
      keep only sanitized examples in this product repo.
- [x] Publish the host-side side repo in a flatter partially rendered authority
      layout: `/var/lib/proxnix/authority/site.nix`,
      `/var/lib/proxnix/authority/containers/<vmid>/...`, and
      `/var/lib/proxnix/authority/publish-revision.json`. The host completes
      `proxmox.nix`, `modules/`, and `flake.nix`; encrypted secrets remain
      under `/var/lib/proxnix/private/containers/<vmid>/`.
- [x] Route workstation post-publish reconcile handoff through
      `proxnix-host api site-updated` instead of calling lower-level host
      reconcile commands directly. Dry-run publish uses `proxnix-host api plan`.
- [x] Make successful non-dry-run workstation publishes trigger the host API
      reconciliation handoff by default, with `--no-reconcile` as the explicit
      file-sync-only escape hatch.
- [x] Narrow Nix package source filtering so host packages do not include the
      whole repository.
- [ ] Add a dev shell/check package for local host-controller tests and static
      checks on Darwin and Linux developer machines.
- [x] Refresh legacy create-lxc next-step text so it points at current
      reconcile/start/status workflows instead of old bootstrap instructions.

## Validation

- [ ] Re-run `nix build --no-link --print-out-paths .#proxnix-host-controller`
      after adding the new files to git; the package source filter intentionally
      excludes untracked files and currently does not see `secret_bundle.rs`.
- [x] Extend the real Proxmox LXC exercise harness so one run covers dry-run
      publish planning, publish-triggered host API reconciliation, build-only
      reconciliation before runtime tags, the `start-host` hook entrypoint,
      Proxmox start-hook behavior, online reconciliation, and stopped/offline
      reconciliation.
- [x] `nix flake check --no-build`.
- [x] Host-controller unit tests: `cargo test -p proxnix-host`.
- [x] Rust formatting: `cargo fmt --check`.
- [x] Focused workstation tests: 83 `unittest` tests covering publish, secrets,
      manager API, TUI, Orb site/exercise, doctor, and SSH command behavior.
- [x] ShellCheck for host scripts and workstation packaging script.
- [x] ansible-lint for host playbooks.
- [x] deadnix for Nix sources
- [ ] statix for runtime Nix style warnings

## Remaining Compatibility Debt

- [ ] Rename internal Python modules/functions that still carry `sops_*` names
      even though they now operate on age bundles. They are not part of the
      runtime contract, but the names are confusing.
- [ ] Sweep user-facing docs beyond this tracker so examples use
      `effective.secrets.json`, `secrets.proxnix.json`, `age_identity.age`,
      `embedded-age`, and `PROXNIX_AGE_MASTER_IDENTITY`.
