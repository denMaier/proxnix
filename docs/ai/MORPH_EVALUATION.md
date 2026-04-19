# Using `DBCDK/morph` With `proxnix`

## Conclusion

`morph` is not a good replacement for `proxnix`.

`proxnix` is already in better shape for this project's actual job: managing
NixOS LXC guests from the Proxmox host using a host-render / guest-apply model.

`morph` is a remote deployment tool for existing NixOS hosts over `ssh`/`scp`.
That is a different control-plane model from `proxnix`, which stages guest
state during Proxmox container startup and applies it inside the guest only when
the rendered config hash changes.

## Why `morph` Does Not Replace `proxnix`

`proxnix` is built around:

- Proxmox-owned container metadata
- pre-start rendering under `/run/proxnix/<vmid>/`
- mount-hook syncing into the guest rootfs
- boot-time guest convergence with hash-based rebuild gating
- relay-backed SOPS/age secret staging for containers

That architecture is central to the repo and not something `morph` solves.

Replacing `proxnix` with `morph` would mean losing or reworking core behavior
instead of improving it:

- host-side rendering tied to Proxmox CT config
- boot-time rootfs injection before the guest is fully running
- container-oriented secret relay and identity staging
- guest runtime helper and secret-driver injection through hooks
- the current "restart CT to lock in host-managed state" workflow

## Where `morph` Can Still Help

The right move is to keep `proxnix` as the deployment architecture and borrow a
few operational ideas from `morph`.

## Backlog To Borrow From `morph`

### 1. Add explicit rollout modes for guest apply

`morph` exposes a clear `dry-activate` / `test` / `switch` / `boot` model.

`proxnix` currently always converges with `nixos-rebuild switch` in the
generated boot-time runner. A useful adaptation would be a per-container or
site-level apply mode for safer staged rollout and easier debugging.

Possible shape:

- `applyMode = "switch" | "test" | "boot"`
- optional one-shot override for the next restart
- visible in `proxnix-doctor` and `proxnix-help`

### 2. Add first-class health checks in container config

`morph` treats health checks as part of deployment. `proxnix` already has
`proxnix-doctor`, but its checks are mostly structural and post-factum.

The next useful step is user-defined health checks such as:

- command checks run inside the guest after apply
- HTTP checks run from the host or workstation
- failure reporting that marks the container as changed but unhealthy

Possible shape:

- `healthchecks:` in `proxmox.yaml` or a dedicated file
- `type: command` and `type: http`
- configurable timeout / retries / initial delay

### 3. Add targeting and batching to publish and rollout workflows

`morph` is strong on selecting subsets of hosts. `proxnix-publish` can target
hosts, but the wider workflow is still fairly manual.

Useful additions:

- `--vmid`, `--on`, or `--tag` targeting
- canary restart groups
- limited parallelism for multi-node publish and restart operations
- deterministic ordering for staged changes

### 4. Add a `proxnix plan` command

Today the preview story is split across `--dry-run` on install/publish and hash
comparison at boot.

A `proxnix plan <vmid>` command would add real operational leverage:

- show whether the rendered hash changed
- list changed managed files, helper scripts, container-workload modules, and secret stores
- say whether the next restart will rebuild or no-op
- show whether nested container features are required and missing

This is one of the highest-value borrowings because it improves confidence
without changing the core architecture.

### 5. Add structured deployment metadata

`morph` benefits from explicit per-host metadata. `proxnix` currently infers a
lot from Proxmox config plus filesystem layout.

Adding optional tags or rollout groups would unlock better filtering and
ordering without fighting the current design.

Possible shape:

- `tags: [ "prod", "media", "node-a" ]`
- `rolloutGroup: "canary"`
- `order: 10`

### 6. Add post-apply status recording

`proxnix` already tracks:

- current desired hash
- last applied hash

It would be better to also record:

- last apply timestamp
- last exit status
- last error summary
- last health-check result

That would make `proxnix-doctor` more informative than just reporting a hash
mismatch and sending the operator to `journalctl`.

## What Is Not Worth Borrowing

These are poor fits for this repo:

- replacing the Proxmox hook model with SSH push deployment
- using `morph deploy` as the main guest configuration path
- replacing the current secret relay model with generic file upload semantics

Those changes would work against the core design rather than improving it.

## Recommended Implementation Order

1. `proxnix plan`
2. post-apply status recording
3. configurable health checks
4. rollout/apply modes
5. targeting and batching

## Short Version

Do not adopt `morph` as the deployment engine for this repo.

Keep `proxnix` as the Proxmox-first control plane and borrow a small set of
`morph`-style operational features that improve safety, visibility, and staged
rollout behavior.
