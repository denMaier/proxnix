# reconciler state model implementation plan

## purpose

This plan describes how to move the current host-side reconciler toward an
operational model where Proxmox nodes can keep containers running and
converging even when a shared binary cache is temporarily unavailable.

The shared cache is treated as acceleration and cross-node artifact memory. It
is not a runtime dependency for already-booted or already-current containers.
The container store remains the runtime source for the active system
generation. The Proxmox node remains the builder and delivery agent.

## current repo state

The current branch already contains the host reconciler pivot in progress.

Implemented behavior:

- `host/runtime/bin/proxnix-reconcile` renders the generated authority wrapper.
- The authority exposes cluster-scoped containers and a node view.
- The reconciler evaluates the node manifest.
- Selected containers are skipped when they are not local to the current node.
- `--dry-run` prints planned build, seed, and activation work.
- `--build-only` builds a selected local system and writes status JSON.
- `--seed-only` imports the recorded desired closure into the container store.
- Full `--vmid` reconcile builds, seeds, activates, verifies, and records
  status.
- `--rollback` activates the previous recorded system path.
- The guest-side rebuild service has been removed from the normal activation
  path.
- Workstation deploy can trigger host reconciliation.

Important current limitations:

- Full reconcile still builds before checking whether the container is already
  on the desired system path.
- Cache or build failure is not yet modeled as a first-class non-invasive
  state.
- The status JSON is container-oriented, but there is no explicit per-closure
  state model.
- There is no local journal for pending cache upload, closure protection, or
  retry history.
- Host garbage collection does not yet know which locally built closures must
  be protected until they have been delivered or uploaded.
- Cache reconciliation is not separate from container activation.
- Locality is checked, but the code should re-check it at every destructive
  phase boundary.

Existing operator-facing status lives at:

```text
/var/lib/proxnix/status/<vmid>.json
```

The desired internal state store should be added separately rather than
replacing the JSON status files immediately.

## desired model

For each managed container, the reconciler observes these descriptive states:

- `desired_system`: the system path selected by evaluating the host authority
  for the container.
- `current_system`: the container's live `/run/current-system` target.
- `container_is_local`: whether this Proxmox node currently owns the container
  runtime.
- `host_has_closure`: whether the desired system closure is already present in
  the node's host `/nix/store`.
- `container_has_closure`: whether the desired system closure is present in the
  container's `/nix/store`.
- `shared_cache_has_closure`: whether the desired system closure is available
  from the configured shared cache.
- `pending_cache_upload`: whether this node realized a closure locally that
  should be copied to the shared cache later.
- `protected_by_host_gc_root`: whether this node must keep the closure alive in
  its host store.

The core convergence rule is:

```text
container is converged when current_system equals desired_system
```

The core safety rule is:

```text
do not change the container unless locality was checked immediately before the
operation and the exact desired system closure is available to the container
```

The shared cache must never be required for:

- booting an already valid container generation
- running services from the current generation
- detecting that an already-current container is a no-op
- rolling forward when the node can build the desired closure locally

The shared cache is useful for:

- avoiding repeated builds across nodes
- delivering a closure built by another node
- surviving node-local garbage collection
- prebuilding before activation
- recreating containers with empty stores
- recovering rollback generations that were garbage-collected from a container

## state ownership

The model should keep different kinds of truth separate.

### declarative truth

Declarative truth comes from the published proxnix configuration and generated
host authority.

Examples:

- per-container NixOS modules
- install-layer modules
- site-level modules
- generated Proxmox metadata
- source revision metadata

### placement truth

Placement truth comes from Proxmox, not from proxnix state files.

The current implementation uses `pct status <vmid>` to decide whether a
container is local. A stronger next step is to prefer Proxmox's cluster view
through `pvesh get /cluster/resources --type vm --output-format json`, then use
`pct` only for local operations.

### artifact truth

Artifact truth is represented by Nix store paths and closure availability.

The same store path can exist in:

- the host store
- the container store
- the shared cache

These locations are delivery and availability states, not different desired
systems.

### runtime truth

Runtime truth for a container is the live target of:

```text
/run/current-system
```

The reconciler should verify this after activation before recording a
deployment as current.

### local coordination state

Local coordination state should be node-local. It is useful for retries,
pending uploads, closure protection, and debugging. It should not decide
cluster ownership.

Recommended path:

```text
/var/lib/proxnix/state/proxnix-reconciler.sqlite
```

Keep the existing JSON files as the operator-facing status API:

```text
/var/lib/proxnix/status/<vmid>.json
```

## why sqlite is useful even though it is local

SQLite is useful as a node-local reconciliation journal because the node has
local responsibilities:

- converge containers currently local to this node
- remember closures this node built while the shared cache was unavailable
- protect those closures from host garbage collection
- retry cache uploads later
- record attempt history across systemd timer runs

SQLite should not be treated as a cluster database. If a container floats away,
the new node derives responsibility from Proxmox placement and desired state.
The old node may still upload locally built closures to the shared cache, but it
must not keep acting on the moved container.

JSON alone can represent simple status. SQLite becomes useful when multiple
systemd jobs need atomic updates and queries across many paths, for example:

- find all pending cache uploads
- find all closures protected by local GC roots
- find stale attempts
- avoid duplicate uploads from concurrent timers
- update closure and container observations transactionally

## proposed local database schema

Initial tables should be intentionally small.

```sql
create table container_observations (
  vmid integer primary key,
  node text not null,
  desired_system text,
  current_system text,
  container_is_local integer not null,
  last_phase text,
  last_status text,
  last_error text,
  updated_at text not null
);

create table closure_observations (
  store_path text primary key,
  host_has_closure integer,
  container_has_closure integer,
  shared_cache_has_closure integer,
  pending_cache_upload integer not null default 0,
  protected_by_host_gc_root integer not null default 0,
  gc_root_path text,
  updated_at text not null
);

create table deployment_attempts (
  id integer primary key autoincrement,
  vmid integer not null,
  store_path text,
  phase text not null,
  status text not null,
  error text,
  started_at text not null,
  finished_at text
);

create index deployment_attempts_vmid_idx on deployment_attempts(vmid);
create index closure_pending_upload_idx
  on closure_observations(pending_cache_upload);
```

The JSON status file should remain the stable external surface. It can include
summaries derived from SQLite without exposing the whole journal.

## desired reconcile flow

### phase 1: evaluate desired state

Render the authority and evaluate the selected container.

Outputs:

- `desired_system`
- source revision
- manifest metadata

Failure behavior:

- record `eval-failed`
- do not build
- do not start the container
- do not seed
- do not activate

### phase 2: observe locality

Check whether this node currently owns the container runtime.

Behavior:

- if `container_is_local=false`, record `skip-not-local` and stop
- if `container_is_local=true`, continue

The implementation should eventually prefer Proxmox cluster placement for this
decision and keep `pct` for local execution.

### phase 3: observe current system

Read the container's current system path:

```text
pct exec <vmid> -- readlink -f /run/current-system
```

If the container is stopped, the reconciler may start it only after it has
already confirmed that the container is local.

Behavior:

- if `current_system == desired_system`, record `noop-current` and stop
- no build is needed
- no shared cache access is needed
- no closure import is needed
- no activation is needed

This is the key optimization that makes shared cache failure irrelevant for
already-current containers.

### phase 4: realize desired closure on the host

If the container is stale, the desired closure must be available somewhere
before it can be imported.

Behavior:

- check whether the host already has the desired closure
- if present, continue
- if missing, run `nix build` for the exact desired system attr
- normal Nix substituter configuration may use the shared cache
- if the shared cache is unavailable but local build succeeds, continue
- if realization fails, record `build-failed` and leave the container untouched

When realization succeeds:

- create or update a host GC root before seed/activation
- record `host_has_closure=true`
- record `protected_by_host_gc_root=true`
- record `pending_cache_upload=true` unless shared cache presence is verified

Recommended GC root path:

```text
/var/lib/proxnix/gcroots/deploy/<vmid>-desired
```

### phase 5: re-check locality before seed

Before importing into the container, check locality again.

Behavior:

- if locality was lost, record `lost-locality`
- keep the host closure protected if it was built locally
- leave `pending_cache_upload=true` if shared cache does not have it
- do not seed
- do not activate

### phase 6: seed closure into the container

Import the closure from the host store into the container store:

```text
nix-store --query --requisites <desired_system>
nix-store --export ...
pct exec <vmid> -- nix-store --import
```

Then verify that the activation script exists:

```text
pct exec <vmid> -- test -x <desired_system>/bin/switch-to-configuration
```

Failure behavior:

- record `seed-failed`
- do not activate
- keep the host GC root for retry
- leave the container's current generation unchanged

### phase 7: re-check locality before activation

Before running `switch-to-configuration`, check locality again.

Behavior:

- if locality was lost, record `lost-locality`
- do not activate
- keep/upload the host closure as appropriate

### phase 8: activate exact desired system

Run:

```text
pct exec <vmid> -- <desired_system>/bin/switch-to-configuration switch
```

Failure behavior:

- record `activation-failed`
- do not mark `current_system` as desired
- preserve rollback metadata

### phase 9: verify runtime state

Read `/run/current-system` again.

Behavior:

- if it equals `desired_system`, record `activated`
- if it differs, record `verify-failed`

Only this verification should allow the status file to say the current system is
the desired system.

## cache reconciliation flow

Cache reconciliation should be separate from CT activation.

Add:

```text
host/runtime/bin/proxnix-cache-reconcile
host/runtime/systemd/proxnix-cache-reconcile.service
host/runtime/systemd/proxnix-cache-reconcile.timer
```

The cache reconciler reads the local SQLite database for closures with:

```text
pending_cache_upload=true
```

For each closure:

1. verify the host still has the closure
2. if missing, try to realize it again or record `cache-upload-blocked`
3. copy it to the shared cache with `nix copy --to <cache> <store_path>`
4. verify shared cache availability with `nix path-info --store <cache>`
5. clear `pending_cache_upload`
6. release the host GC root if policy allows

This job should be idempotent per store path. Multiple nodes may attempt to
upload the same closure. That is acceptable if the shared cache backend handles
idempotent writes. If the backend does not, add a simple per-store-path lock or
lease later.

## host garbage collection policy

Host GC must not collect closures that are still needed for local coordination.

Protected paths:

- active deployment target
- pending cache upload
- currently seeded but not yet activated target
- optional last successful deployment for fast local rollback

Unprotected paths may be collected. If needed again, they can be substituted
from the shared cache or rebuilt.

The host GC job should consult the local database or GC root directory rather
than attempting to infer intent from status JSON alone.

## container garbage collection policy

The container store is runtime-critical.

Container GC should rely on standard NixOS roots:

- `/run/current-system`
- system profile generations
- any explicit rollback roots if added later

If an old generation is collected from the container store, rollback may still
work if the host or shared cache can re-import the previous closure. If neither
has it, rollback is unavailable until it can be rebuilt.

## failure states to implement

The reconciler should use explicit phases and statuses.

Recommended status names:

- `eval-failed`
- `skip-not-local`
- `noop-current`
- `realizing`
- `build-failed`
- `realized`
- `lost-locality`
- `seeding`
- `seed-failed`
- `seeded`
- `activating`
- `activation-failed`
- `verify-failed`
- `activated`
- `pending-cache-upload`
- `cache-uploaded`
- `cache-upload-blocked`

These states should be visible in the JSON status summary and recorded in the
SQLite attempt journal.

## race conditions and handling

### container migrates before build

The node should stop if it no longer owns the container. Optional future
prebuild mode can still realize the closure for cache warming, but normal
reconcile should stop.

### container migrates during build

The build may finish on the old node. The old node must re-check locality before
seeding. If locality is lost, it should keep the closure protected and upload it
later, but not touch the container.

### container migrates after seed

The old node must re-check locality before activation. If locality is lost, it
must not activate. The new node can observe the container store and current
system when it reconciles.

### shared cache fails during no-op detection

No impact. No-op detection requires only desired evaluation and the container's
current system path.

### shared cache fails during realization

If local build succeeds, deployment continues and the closure is marked pending
for later cache upload. If local build fails, deployment stops before touching
the container.

### shared cache returns after local build

The cache reconciler uploads the locally built closure, verifies it, clears
pending upload state, and releases eligible GC roots.

### host GC races with cache upload

Host GC roots must be created before marking a closure pending for upload. Cache
reconciliation clears the pending state only after upload verification.

### duplicate uploads from multiple nodes

The shared cache should ideally accept idempotent uploads. If not, use a
per-store-path lock. This is a cache coordination issue, not a container
activation issue.

### activation succeeds but verification fails

Do not mark the deployment as current. Record `verify-failed` and leave rollback
metadata intact.

## implementation sequence from current repo state

### step 1: desired path no-op check

Change `proxnix-reconcile` so full reconcile evaluates the desired system path
before building and compares it to the container's current system.

Expected behavior:

- local and already-current container exits with `noop-current`
- no `nix build`
- no `nix-store --export`
- no activation

Tests:

- add a test proving full reconcile skips build when current system already
  matches the evaluated desired system
- add a test proving cache/build commands are not called in this path

### step 2: explicit build failure status

Wrap host realization so build failure records a non-invasive status.

Expected behavior:

- build failure writes `lastBuildStatus=failed`
- deployment status remains unchanged or becomes `build-failed`
- current system remains the previous observed current system
- no seed or activation is attempted

Tests:

- simulate `nix build` failure
- assert no `pct exec ... nix-store --import`
- assert status preserves current system

### step 3: add host GC roots

After successful realization, create a host GC root for the desired system before
seeding.

Expected behavior:

- desired closure is protected while seed, activation, or cache upload is
  pending
- root is updated atomically

Tests:

- assert a GC root symlink is created for the desired path
- assert it is retained after seed or activation failure

### step 4: add local SQLite journal

Add a small Python helper or shell wrapper for state updates.

Suggested path:

```text
host/runtime/lib/proxnix_reconciler_state.py
```

Suggested command wrapper:

```text
host/runtime/bin/proxnix-reconciler-state
```

Expected behavior:

- initialize schema
- record container observations
- record closure observations
- record deployment attempts
- query pending cache uploads

Tests:

- schema creation
- idempotent observation updates
- pending upload query

### step 5: record descriptive state fields

Update `proxnix-reconcile` to record:

- `desired_system`
- `current_system`
- `container_is_local`
- `host_has_closure`
- `container_has_closure`
- `shared_cache_has_closure`
- `pending_cache_upload`
- `protected_by_host_gc_root`

Keep the existing camelCase JSON fields for compatibility if needed, but add
the descriptive fields internally and optionally expose a summarized version in
status JSON.

Tests:

- status contains enough information to explain no-op, build failure, seed
  failure, and activation success

### step 6: re-check locality at phase boundaries

Add locality checks immediately before:

- seed
- activation
- final status update that marks current as desired

Expected behavior:

- if locality is lost, stop with `lost-locality`
- keep host closure state for cache upload
- do not modify the moved container

Tests:

- fake `pct status` succeeding initially and failing before seed
- fake locality loss before activation

### step 7: add cache reconciliation command

Implement `proxnix-cache-reconcile`.

Expected behavior:

- reads pending uploads from local state
- verifies host closure availability
- runs `nix copy --to`
- verifies shared cache path-info
- clears pending state
- releases eligible GC roots

Tests:

- successful upload clears pending state
- failed upload keeps pending state and GC root
- missing host closure records blocked state

### step 8: add cache reconciliation systemd units

Install:

```text
proxnix-cache-reconcile.service
proxnix-cache-reconcile.timer
```

Expected behavior:

- timer retries pending uploads independently of CT deployment
- cache outage does not block the main reconciler from local builds

Tests:

- package/install scripts include the new unit files
- uninstall removes or disables them consistently

### step 9: prefer Proxmox cluster placement for locality

Use Proxmox's cluster API for placement truth:

```text
pvesh get /cluster/resources --type vm --output-format json
```

Expected behavior:

- `container_is_local` is based on the reported Proxmox node when available
- fallback to `pct status` remains for limited environments
- `pct` remains the execution tool for local start, exec, import, and activate

Tests:

- placement JSON reports remote node and reconcile skips
- placement JSON reports local node and reconcile proceeds
- fallback path still works when `pvesh` is unavailable

### step 10: update docs and operator commands

Document:

- the descriptive state model
- shared cache as optional acceleration
- no-op detection before build
- local build with pending cache upload
- host and container GC policies
- failure states
- race handling for floating containers

Update:

- `docs/concepts/architecture.md`
- `docs/reference/commands.md`
- `docs/getting-started/first-container.md`
- `docs/reference/files-and-directories.md`

## acceptance criteria

The desired model is implemented when:

- an already-current local container reconciles without build or cache access
- a stale local container can activate from a host-local closure
- a stale local container can activate after a local build while shared cache is
  unavailable
- failed build/cache access leaves the container untouched
- locally built closures are protected until uploaded or policy releases them
- cache upload retry is handled by a separate command and timer
- locality is re-checked before seed and activation
- status explains what happened using descriptive phase names
- JSON status remains operator-friendly
- SQLite remains a local coordination journal, not cluster authority

## design invariant

The reconciler may retry, upload, rebuild, or garbage-collect around the
container, but it should only change the container after proving:

- the container is still local to this node
- the desired system path is known
- the desired closure is available to the container
- activation targets exactly the desired system path
- verification confirms the live current system equals the desired system

Everything else is coordination state.
