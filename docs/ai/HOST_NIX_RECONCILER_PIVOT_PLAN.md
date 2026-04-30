# Host Nix Reconciler Pivot Plan

## Goal

Pivot proxnix from guest-side Nix evaluation and boot-time self-rebuilds to a
Proxmox-node-side reconciler.

Target architecture:

```text
Workstation
  owns, edits, and publishes the configuration repo

Proxmox node
  consumes the configuration repo
  evaluates Nix
  builds each target LXC system closure
  prepares and recreates LXC containers
  seeds required Nix store paths
  activates exact NixOS system paths

LXC container
  consumes an already-built NixOS closure
  runs switch-to-configuration
  does not evaluate desired config as the primary path
```

The Proxmox node must be able to recreate a managed LXC from local knowledge as
long as the node's local authority state is intact.

## Non-Goals

- Do not keep a long-term compatibility mode for guest-side rebuilds.
- Do not require Nix on the workstation.
- Do not require external CI, Cachix, Attic, or a separate builder to reach the
  core local workflow.
- Do not make the target LXC responsible for evaluating its desired system.
- Do not depend on guest networking for normal deployment.

## Core Decision

Make Nix a required part of the proxnix host runtime on Proxmox nodes.

proxnix already modifies the Proxmox host by installing LXC hooks, helper
scripts, host-side systemd units, and shared state under `/var/lib/proxnix`.
Adding Nix is therefore an extension of the existing host runtime, not a move
from pristine Proxmox to managed Proxmox.

The new convergence invariant is:

```text
A container is converged when:
  readlink -f /run/current-system inside the CT
  ==
  the desired NixOS system closure path selected by the host manifest
```

Config hashes may remain useful as metadata or cheap change indicators, but the
authoritative activation comparison is the NixOS system closure path.

## Current Model To Replace

The current flow is:

```text
Proxmox host pre-start hook
  renders guest config into /run/proxnix/<vmid>/

Proxmox host mount hook
  copies and bind-mounts managed files into the guest rootfs

Guest systemd unit
  compares current-config-hash and applied-config-hash
  bootstraps channels when needed
  runs nixos-rebuild switch when the hash changed
```

Problems with this model:

- Guest boot timing affects convergence.
- Guest networking and DNS can block channel bootstrap or builds.
- The guest must evaluate and build its own desired system.
- Recovery is weak when the CT is corrupted or cannot boot far enough.
- A launch-only staging model makes live deployment awkward.
- The real desired state is a NixOS system closure, not a hand-rolled config
  tree hash.

## Current Code Structure

The pivot should be implemented as an evolution of the current host/workstation
split, not as a parallel product.

### Host Runtime Today

Installed by `host/install/install.sh`:

- `/usr/share/lxc/config/nixos.common.conf`
- `/usr/share/lxc/config/nixos.userns.conf`
- `/usr/share/lxc/hooks/nixos-proxnix-prestart`
- `/usr/share/lxc/hooks/nixos-proxnix-mount`
- `/usr/share/lxc/hooks/nixos-proxnix-poststop`
- `/usr/local/lib/proxnix/nixos-proxnix-common.sh`
- `proxnix-host pve-conf-to-nix`
- `/usr/local/lib/proxnix/proxnix-secrets-guest`
- `/usr/local/sbin/proxnix-create-lxc`
- `/usr/local/sbin/proxnix-doctor`
- `proxnix-gc.{service,timer}`

Current durable host state:

```text
/var/lib/proxnix/
  base.nix
  common.nix
  configuration.nix
  security-policy.nix
  site.nix
  publish-revision.json
  containers/<vmid>/
    dropins/
    templates/
    secret-groups.list
  private/
    containers/<vmid>/
      effective.sops.yaml
      age_identity.sops.yaml
/etc/proxnix/
  host_relay_identity
```

`host/runtime/lxc/hooks/nixos-proxnix-prestart` currently:

- reads `/etc/pve/lxc/<vmid>.conf`
- validates shared files under `/var/lib/proxnix`
- renders Proxmox CT config with `proxnix-host pve-conf-to-nix`
- copies `site.nix`, selected templates, and `containers/<vmid>/dropins`
- computes `current-config-hash`
- generates `proxnix-apply-config-runner`
- generates `proxnix-apply-config.service`
- decrypts host-relay container identities for mount-time staging
- writes all staging data under `/run/proxnix/<vmid>`

`host/runtime/lxc/hooks/nixos-proxnix-mount` currently:

- consumes `/run/proxnix/<vmid>`
- writes `/etc/nixos/configuration.nix`
- bind-mounts `/var/lib/proxnix/config`
- bind-mounts `current-config-hash` and `vmid`
- copies the generated runner, guest helper scripts, service files, and secrets
- registers Podman secrets metadata
- leaves `applied-config-hash` as guest-local writable state

`host/runtime/bin/proxnix-create-lxc` currently owns CT creation ergonomics. It
already detects templates/storage, validates host install health through
`proxnix-doctor --host-only`, runs `pct create --ostype nixos`, creates
`/var/lib/proxnix/containers/<vmid>/dropins`, and optionally starts the CT.

`host/runtime/bin/proxnix-doctor` currently checks the install, relay cache,
guest-injected files, `proxnix-apply-config.service`, and config-hash
convergence. It is the right place to add Nix/reconciler checks before removing
the guest hash checks.

`host/runtime/nix/{configuration,base,common,security-policy}.nix` currently
defines the guest baseline and imports the mounted `/var/lib/proxnix/config`
tree. This module set should remain useful, but it must become part of the
host-evaluated NixOS configuration instead of a config tree evaluated by the
guest.

### Workstation Runtime Today

`workstation/cli/src/proxnix_workstation/publish_cli.py` currently owns the
host publish path:

- reads `WorkstationConfig` from `workstation/cli/src/proxnix_workstation/config.py`
- defaults `PROXNIX_REMOTE_DIR` to `/var/lib/proxnix`
- defaults `PROXNIX_REMOTE_PRIV_DIR` to `/var/lib/proxnix/private`
- materializes the site repo's Git `HEAD`, not dirty worktree state
- publishes only `site.nix`, `containers/`, compiled secrets, relay identities,
  and `publish-revision.json`
- supports `--vmid` and `--config-only`
- uses rsync over SSH

`workstation/cli/src/proxnix_workstation/publish_tree.py` mirrors the
config-only tree used by `proxnix config plan-tree`. It copies `site.nix` and
`containers/`, but it does not understand flakes, node manifests, or
host-selected system closures.

`workstation/cli/src/proxnix_workstation/cli.py` forwards `proxnix publish`,
`proxnix sync`, and `proxnix diff` to the publish implementation. There is no
`deploy` command yet; the closest current command is `publish`.

### Tests To Extend

The most relevant existing test files are:

- `workstation/cli/tests/test_publish_cli.py`
- `workstation/cli/tests/test_doctor_cli.py`
- `workstation/cli/tests/test_manager_api.py`
- `workstation/cli/tests/test_exercise_cli.py`
- `workstation/cli/tests/test_orb_exercise_cli.py`

There are currently no host-shell unit tests for `proxnix-create-lxc`,
`proxnix-doctor`, or the LXC hooks. New host reconciler logic should therefore
be structured so most behavior is testable as pure shell functions or moved
into a small Python helper with normal tests.

## Concrete Pivot Strategy

Do the pivot in two compatibility layers, then remove the old activation path.

### Layer 1: Add Host Reconciler Beside Existing Hooks

Add host Nix, the authority checkout, manifest evaluation, closure build,
status writing, and `proxnix-reconcile` without first deleting the current LXC
hooks. During this layer, existing containers can still boot and self-apply
through `proxnix-apply-config.service`, while the reconciler can be exercised
with `--dry-run`, then build-only, then activate-one-CT modes.

### Layer 2: Make Reconciler The Primary Activation Owner

Once `proxnix-reconcile --vmid <id>` can build, seed, activate, verify, and
write status for one CT, change the hooks so they stop generating the guest
`nixos-rebuild` runner. The mount hook should keep only the pieces still needed
at runtime:

- VMID marker
- compiled secret stores and decrypted identities
- Podman secret metadata registration, unless replaced by Nix activation
- optional diagnostics files

At this point, config hashes are diagnostic metadata only. The reconciler status
file and `/run/current-system` comparison are authoritative.

### Layer 3: Collapse The Relay Cache Into Authority

The current `/var/lib/proxnix` relay cache can become one of:

1. a backwards-compatible input tree that is wrapped by a generated flake under
   `/var/lib/proxnix/authority`, or
2. the authority checkout itself, if the site repo is migrated to flake layout.

Prefer option 1 for the first implementation because it avoids making every
existing site repo migrate before the host reconciler can be tested. The
workstation can keep publishing `site.nix`, `containers/`, and private compiled
secrets while the host installer provides a generated flake wrapper that imports
the same modules.

## Target Roles

### Workstation

The workstation remains the user-facing control plane.

Responsibilities:

- edit the configuration repo
- manage secrets UX
- publish or push the configuration repo to Proxmox nodes
- trigger reconciles
- show status

The workstation should not need Nix for the default workflow.

Example commands:

```bash
proxnix publish
proxnix deploy --host root@pve1
proxnix deploy --host root@pve1 --vmid 101
proxnix status --host root@pve1
```

### Proxmox Node

The Proxmox node becomes the deployment executor.

Responsibilities:

- keep a local configuration authority checkout
- evaluate the node-specific Nix manifest
- build each target CT's NixOS system closure
- create or repair LXC containers from local state
- seed Nix closures into target CTs
- activate exact system paths inside target CTs
- record deployment status and rollback metadata

Required local state:

```text
/var/lib/proxnix/authority
/var/lib/proxnix/status
/var/lib/proxnix/containers
/var/lib/proxnix/private
/etc/pve/lxc
/nix/store
```

### LXC Guest

The guest becomes an activation target.

Responsibilities:

- contain a NixOS rootfs and `/nix/store`
- receive or share required store paths
- run `switch-to-configuration`
- provide health/status signals after activation

The guest should not:

- bootstrap Nix channels as part of normal convergence
- run `nixos-rebuild switch` as the primary deployment path
- evaluate the desired configuration as the primary deployment path

## Authority Repo Contract

The configuration repo should expose a node-specific manifest that a Proxmox
node can evaluate locally.

Example layout:

```text
proxnix-site/
  flake.nix
  flake.lock
  nodes/
    pve1.nix
  containers/
    101.nix
    102.nix
  modules/
  secrets/
```

Required flake outputs should provide both:

- concrete NixOS configurations
- a node manifest that maps VMIDs to desired system closures and lifecycle data

Conceptual shape:

```nix
{
  nixosConfigurations.ct101 = nixpkgs.lib.nixosSystem {
    system = "x86_64-linux";
    modules = [
      ./containers/101.nix
    ];
  };

  proxnix.nodes.pve1.containers."101" = {
    vmid = 101;
    hostname = "ct101";
    system = self.nixosConfigurations.ct101.config.system.build.toplevel;
    pve = {
      memory = 2048;
      cores = 2;
      rootfs = "local-lvm:8";
      net0 = "...";
    };
  };
}
```

The node should evaluate the manifest with a command similar to:

```bash
nix eval --json /var/lib/proxnix/authority#proxnix.nodes.pve1
```

The reconciler should build each desired system closure explicitly:

```bash
nix build \
  /var/lib/proxnix/authority#nixosConfigurations.ct101.config.system.build.toplevel
```

### Initial Compatibility Contract

The repo does not currently publish that layout. The workstation publishes this
legacy tree to `PROXNIX_REMOTE_DIR`:

```text
/var/lib/proxnix/
  site.nix
  publish-revision.json
  containers/
  private/
```

The first concrete step is therefore to create a host-owned generated authority
wrapper:

```text
/var/lib/proxnix/authority/
  flake.nix                 # installed/generated by proxnix
  flake.lock                # generated on host, pinned by installer/reconciler policy
  modules/
    proxnix-guest-base.nix  # imports host/runtime/nix modules
  generated/
    node-manifest.nix       # generated from /var/lib/proxnix + /etc/pve/lxc
```

The wrapper lets the host evaluate existing published data while the public site
contract moves toward native flakes.

Generated manifest inputs:

- `/var/lib/proxnix/site.nix`, if present
- `/var/lib/proxnix/containers/<vmid>/dropins/*.nix`
- `/var/lib/proxnix/containers/<vmid>/templates/*.template`
- `/etc/pve/lxc/<vmid>.conf`, converted with `proxnix-host pve-conf-to-nix`
- `/var/lib/proxnix/publish-revision.json`

Generated manifest output shape:

```nix
{
  proxnix.nodes.${nodeName}.containers.${vmid} = {
    inherit vmid;
    hostname = "...";
    sourceRevision = {
      commit = "...";
      branch = "...";
      dirtyWorktreeIgnored = false;
    };
    systemAttr =
      "nixosConfigurations.ct${vmid}.config.system.build.toplevel";
    system = self.nixosConfigurations."ct${vmid}".config.system.build.toplevel;
    pve = {
      ostype = "nixos";
      memory = 2048;
      swap = 512;
      cores = 2;
      rootfs = "local-lvm:vm-101-disk-0,size=8G";
      net0 = "name=eth0,bridge=vmbr0,ip=dhcp";
      unprivileged = true;
      features = "nesting=1,keyctl=1";
    };
  };
}
```

For the compatibility wrapper, the NixOS module list for each CT should be:

```nix
[
  ./modules/proxnix-guest-base.nix
  /var/lib/proxnix/site.nix                 # optional
  ./generated/containers/${vmid}/proxmox.nix
  /var/lib/proxnix/containers/${vmid}/dropins/*.nix
]
```

`host/runtime/nix/configuration.nix` currently imports from
`/var/lib/proxnix/config/managed`, which only exists inside a mounted guest. The
wrapper cannot reuse that file unchanged. It should reuse the modules it points
at (`base.nix`, `common.nix`, `security-policy.nix`) and construct the imports
directly from host paths.

### Target Native Contract

After the compatibility wrapper works, the native site contract should move the
flake to the site repo itself:

- workstation publishes or git-syncs the full flake repo into
  `/var/lib/proxnix/authority`
- `PROXNIX_REMOTE_DIR` either defaults to `/var/lib/proxnix/authority` or is
  split into explicit `PROXNIX_AUTHORITY_DIR` and legacy relay dirs
- existing secret publish targets remain under `/var/lib/proxnix/private` unless
  secrets are deliberately moved into the authority repo
- `proxnix.nodes.<node>` becomes user-visible and documented

## Host Nix Installation

The host installer should make Nix a first-class proxnix dependency.

Installer responsibilities:

- install the Nix daemon on Debian/Proxmox
- enable flakes and nix-command
- ensure root can build and copy closures
- create `/var/lib/proxnix/authority`
- create `/var/lib/proxnix/status`
- install `proxnix-reconcile`
- install `proxnix-reconcile.service`
- install `proxnix-reconcile.timer`

Doctor checks:

- `nix --version`
- Nix daemon is running
- flakes and nix-command are available
- `/nix` has enough free space
- authority repo exists
- node manifest evaluates
- at least one CT system can be built
- `pct` is available
- closure seeding into a test CT works

Concrete file changes:

- Update `host/install/install.sh`.
  - Add `install_nix_if_missing` or fail with a clear instruction behind an
    explicit `--install-nix` flag.
  - Create `/var/lib/proxnix/authority`, `/var/lib/proxnix/status`,
    `/var/lib/proxnix/containers`, `/var/lib/proxnix/private`, and
    `/run/proxnix` with existing permissions discipline.
  - Install `host/runtime/bin/proxnix-reconcile` to
    `/usr/local/sbin/proxnix-reconcile`.
  - Install `host/runtime/bin/proxnix-authority-render` or equivalent helper if
    manifest generation is split out.
  - Install `host/runtime/systemd/proxnix-reconcile.service`.
  - Install `host/runtime/systemd/proxnix-reconcile.timer`.
  - Add the new files to `/usr/local/lib/proxnix/install-manifest.txt`.
- Update `host/install/uninstall.sh` and `host/packaging/debian/postrm` so the
  installed command and systemd units are removed with the rest of the host
  runtime. Keep `/var/lib/proxnix/authority`, `/var/lib/proxnix/status`, and
  `/nix` as data unless a destructive purge mode is explicitly added.
- Update `host/runtime/bin/proxnix-doctor`.
  - Host-only checks should validate `nix`, daemon availability,
    `experimental-features`, `/nix` free space, authority dir, status dir,
    reconciler command, and timer unit.
  - Container checks should compare status `desiredSystem` with
    `pct exec <vmid> -- readlink -f /run/current-system`.
  - The old `current-config-hash` / `applied-config-hash` check should be
    downgraded to legacy mode once reconciler status exists.
- Update packaging scripts under `host/packaging/` so the deb includes the new
  runtime command and units.

## Reconciler

Initial implementation should be a boring shell script:

```text
host/runtime/bin/proxnix-reconcile
```

Core modes:

```bash
proxnix-reconcile
proxnix-reconcile --vmid 101
proxnix-reconcile --dry-run
proxnix-reconcile --rollback --vmid 101
```

Responsibilities:

1. Take a global lock.
2. Update the local authority repo.
3. Evaluate the node manifest.
4. Build desired systems.
5. For each selected container, take a per-CT lock.
6. Ensure the CT exists and is prepared.
7. Seed the desired closure into the CT.
8. Activate the desired system path.
9. Verify convergence.
10. Write status and rollback metadata.

Concrete implementation shape:

```text
host/runtime/bin/proxnix-reconcile
host/runtime/bin/proxnix-authority-render
host/runtime/systemd/proxnix-reconcile.service
host/runtime/systemd/proxnix-reconcile.timer
```

`proxnix-reconcile` should be shell for direct host integration with `pct`,
`nix`, `flock`, and `systemctl`. If JSON handling grows beyond simple `jq`
queries, move manifest/status manipulation into a small Python helper under
`host/runtime/lib/` and keep the shell script as orchestration.

Required command dependencies:

- `nix`
- `nix-store`
- `pct`
- `pvesh` or `pct config`
- `flock`
- `jq`
- `systemctl`
- `readlink`

Suggested functions:

```bash
usage
log
die
json_escape_or_python_status_write
take_global_lock
take_container_lock
render_authority
eval_node_manifest
select_manifest_containers
build_system
ensure_container
ensure_container_running_for_exec
seed_closure
current_system
activate_system
verify_system
write_status
rollback_container
```

Lock paths:

```text
/run/proxnix/reconcile.lock
/run/proxnix/reconcile-<vmid>.lock
```

Build outputs should be captured with `--print-out-paths`:

```bash
system="$(
  nix build \
    --print-out-paths \
    "/var/lib/proxnix/authority#${system_attr}"
)"
```

The dry run should not build by default. It should render/evaluate the manifest,
compare desired metadata with `/etc/pve/lxc/<vmid>.conf`, read existing status
files, and print actions:

```text
101 build /nix/store/... unknown until build
101 create missing CT
101 seed desired closure
101 activate desired system
```

Add an optional `--dry-run --build` later if build validation without activation
is useful.

Systemd timer:

```ini
[Timer]
OnBootSec=2m
OnUnitActiveSec=5m
Persistent=true
```

Manual runs and workstation-triggered runs should use the same reconciler.

The service should be oneshot:

```ini
[Unit]
Description=Reconcile proxnix managed NixOS LXCs
After=network-online.target pve-cluster.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/proxnix-reconcile
```

The workstation trigger should call the same command over SSH:

```bash
ssh root@pve1 proxnix-reconcile --vmid 101
```

## Closure Seeding

The host must ensure the target CT has every store path required by the desired
system closure.

Preferred local transport:

```bash
nix-store --query --requisites "$system" > closure.txt
nix-store --export $(cat closure.txt) \
  | pct exec "$vmid" -- nix-store --import
```

This avoids guest networking and avoids requiring SSH inside the target CT.

Alternative transports can be added later only if they simplify operations:

- `nix copy --to ssh://...`
- host-local binary cache
- shared store strategy

The default should remain independent of guest networking.

Important implementation detail: the host-built system path is a host
`/nix/store/...` path string, and activation inside the guest uses the same path
string only after every requisite path has been imported into the guest store.
Before activation, verify the target path exists inside the guest:

```bash
pct exec "$vmid" -- test -x "$system/bin/switch-to-configuration"
```

For large closures, avoid expanding too many arguments into one command. Use
`xargs` or a temporary requisites file:

```bash
closure_file="/run/proxnix/closure-${vmid}.txt"
nix-store --query --requisites "$system" > "$closure_file"
xargs nix-store --export < "$closure_file" \
  | pct exec "$vmid" -- nix-store --import
```

If `pct exec ... nix-store --import` is not available in early boot or on a
freshly created CT, the fallback should mount the rootfs and import with
`nix-store --store "$rootfs/nix/store"` only if that path is proven reliable on
Proxmox/Nix. Do not make that fallback the primary path until tested.

## Activation

Activation uses the exact system path built on the host:

```bash
pct exec "$vmid" -- "$system/bin/switch-to-configuration" switch
```

Before activation, the reconciler can skip no-op containers:

```bash
current="$(
  pct exec "$vmid" -- readlink -f /run/current-system 2>/dev/null || true
)"

if [ "$current" = "$system" ]; then
  exit 0
fi
```

After activation, verify:

```bash
pct exec "$vmid" -- test "$(readlink -f /run/current-system)" = "$system"
```

Then write status.

`pct exec` requires the CT to be running. For a stopped but intact CT, the
reconciler should:

1. start it with `pct start <vmid>`
2. wait until `pct exec <vmid> -- true` succeeds
3. seed the closure
4. activate the desired system
5. leave it running by default

A later flag can add "restore prior power state", but the first implementation
should prefer explicit convergence over hidden stop/start behavior.

## Container Creation And Recreation

The reconciler owns lifecycle for managed CTs.

For each manifest container:

1. Check whether `/etc/pve/lxc/<vmid>.conf` exists.
2. If missing, create the CT from manifest data.
3. Ensure the rootfs is present and mountable.
4. Prepare the rootfs for NixOS activation.
5. Seed the desired system closure.
6. Start the CT when needed.
7. Activate the desired system path.

This is the recovery property the pivot is meant to unlock: a corrupt or missing
target CT can be recreated by the Proxmox node from local authority state.

Current reusable code: `host/runtime/bin/proxnix-create-lxc` already knows how
to create a NixOS CT from CLI flags. Do not duplicate its template/storage
detection immediately. Instead, refactor or wrap it:

- Add non-interactive flags that map directly from manifest `pve`.
- Add `--no-doctor` or narrower host checks so the reconciler can avoid nested
  full doctor runs.
- Add `--json-plan` only if shell parsing of plan output becomes fragile.
- Keep the existing interactive behavior for human use.

Manifest-to-`pct create` mapping:

```text
pve.hostname      -> --hostname
pve.template      -> --template, optional at first
pve.storage/disk  -> --storage/--disk or rootfs parsing
pve.memory        -> --memory
pve.swap          -> --swap
pve.cores         -> --cores
pve.net0          -> --net0 fields or bridge/ip/gw normalized fields
pve.unprivileged  -> --unprivileged
pve.features      -> --features
```

For phase 1, require an existing CT and treat creation as a planned future
action. For the first recreation milestone, require enough manifest fields to
call `proxnix-create-lxc --yes --no-start`; then start, seed, and activate.

Do not call `pct destroy` automatically during normal reconcile. Replacement of
an existing CT should require an explicit flag such as:

```bash
proxnix-reconcile --vmid 101 --recreate-missing
proxnix-reconcile --vmid 101 --replace-corrupt --yes
```

## Status Files

Write machine-readable status under:

```text
/var/lib/proxnix/status/<vmid>.json
```

Suggested shape:

```json
{
  "vmid": 101,
  "hostname": "ct101",
  "sourceRevision": "git-sha",
  "desiredSystem": "/nix/store/...-nixos-system-ct101-...",
  "currentSystem": "/nix/store/...-nixos-system-ct101-...",
  "previousSystem": "/nix/store/...-nixos-system-ct101-...",
  "lastBuildStatus": "ok",
  "lastDeployStatus": "ok",
  "lastError": null,
  "activatedAt": "2026-04-26T12:00:00Z"
}
```

The workstation and Manager should read these files through host commands rather
than infer status from guest internals.

Add a host command interface before teaching Manager to read status:

```bash
proxnix-reconcile --status
proxnix-reconcile --status --vmid 101
```

This keeps workstation code from depending on direct file paths, permissions, or
future status layout changes. The command can initially concatenate validated
JSON files from `/var/lib/proxnix/status`.

Status write rules:

- write to `/var/lib/proxnix/status/<vmid>.json.tmp`
- `fsync` if implemented in Python, otherwise keep the file small and `mv`
  atomically
- preserve `previousSystem` from the last successful deployment
- record failed build/deploy attempts without overwriting the last known
  successful `currentSystem`
- include `manifestHash` or `manifestPath` to make no-op debugging cheap

Minimum status fields for phase 1:

```json
{
  "vmid": 101,
  "hostname": "ct101",
  "sourceRevision": null,
  "desiredSystem": "/nix/store/...",
  "currentSystem": null,
  "previousSystem": null,
  "lastBuildStatus": "ok",
  "lastDeployStatus": "not-run",
  "lastError": null,
  "updatedAt": "2026-04-26T12:00:00Z"
}
```

## Rollback

For every successful deployment, record the previous successful system path.

Rollback command:

```bash
proxnix-reconcile --rollback --vmid 101
```

Rollback activation:

```bash
pct exec 101 -- "$previousSystem/bin/switch-to-configuration" switch
```

After rollback, verify `/run/current-system` and update status.

Phase two can add health-check-triggered rollback:

1. Activate desired system.
2. Run configured health check.
3. If health check fails, activate previous system.
4. Mark deployment failed and rollback complete.

## Security And Trust

The Proxmox host will evaluate Nix from the authority repo as root or through a
trusted build path.

Implications:

- the authority repo is trusted infrastructure code
- write access to the authority repo is equivalent to deployment authority
- secrets handling must remain explicit and auditable
- future signed manifest or signed commit verification is valuable

Suggested future hardening:

- require signed Git commits or signed tags
- verify a signed manifest before activation
- restrict reconciler trigger permissions
- separate status-read commands from deploy commands

Concrete near-term hardening:

- The installer-created `/var/lib/proxnix/authority` should be root-owned and
  writable only by root.
- `proxnix publish` should continue to require SSH access as a privileged host
  user for now.
- Do not make `proxnix-reconcile --status` require the same privileges as
  deployment once a non-root status reader is introduced.
- Treat `publish-revision.json` as informational only. It records source Git
  state from the workstation, but it is not an integrity guarantee.

## Workstation Changes

The current workstation command that mutates hosts is `proxnix publish`. The
target UX in this plan adds `deploy` and remote status, but those should reuse
the existing publish and SSH machinery.

Concrete changes:

- Update `workstation/cli/src/proxnix_workstation/config.py`.
  - Add `PROXNIX_AUTHORITY_DIR` only when native authority publishing is ready.
  - Keep `PROXNIX_REMOTE_DIR=/var/lib/proxnix` for the compatibility wrapper.
- Update `workstation/cli/src/proxnix_workstation/publish_cli.py`.
  - Keep publishing the legacy relay tree for the compatibility layer.
  - Add `--reconcile` to run `proxnix-reconcile` after a successful publish.
  - For `--vmid`, call `proxnix-reconcile --vmid <id>`.
  - For `--dry-run --reconcile`, call `proxnix-reconcile --dry-run`.
  - Include reconciler exit code/output in the JSON envelope.
- Update `workstation/cli/src/proxnix_workstation/cli.py`.
  - Add top-level `deploy` as an alias for `publish --reconcile`.
  - Add top-level remote status only after the host status command exists, for
    example `proxnix deploy-status --host root@pve1`.
- Update `workstation/manager/app/shared/capabilities/managerHandlers.ts` and
  related frontend types only after CLI JSON output exists. Manager should call
  the CLI rather than hand-rolling SSH commands.
- Extend `workstation/cli/tests/test_publish_cli.py`.
  - assert `--reconcile` calls the expected SSH command
  - assert `--vmid --reconcile` narrows the remote reconcile
  - assert JSON includes publish and reconcile results

## Host Hook Changes

The hooks should be changed only after activation-by-system-path works.

Keep from current hooks:

- stage cleanup and permission discipline
- VMID validation
- PVE config rendering helper, at least for generated manifest compatibility
- compiled secret and identity materialization
- Podman secret metadata registration if still needed by workloads

Remove or disable from `nixos-proxnix-prestart`:

- generated `proxnix-apply-config-runner`
- generated `proxnix-apply-config.service`
- channel bootstrap logic
- hash-triggered `nixos-rebuild switch`

Remove or disable from `nixos-proxnix-mount`:

- required validation for `current-config-hash`
- required validation for `proxnix-apply-config-runner`
- required validation for `proxnix-apply-config.service`
- install/enable of `proxnix-apply-config.service`
- `applied-config-hash` convergence assumptions

Keep compatibility cleanup for old guests:

- remove legacy service/timer files if present
- leave existing `applied-config-hash` files alone unless they confuse current
  diagnostics
- make `proxnix-doctor` report legacy guest-side apply state as informational
  once reconciler status exists

## Migration Plan

### Phase 1: Host Nix

- Add host Nix install support.
- Add doctor checks for Nix and flakes.
- Document host Nix as required proxnix runtime.

Acceptance:

- `host/install/install.sh --dry-run` reports the new Nix/reconciler assets.
- `proxnix-doctor --host-only` fails clearly when Nix is missing.
- `proxnix-doctor --host-only` passes on a Proxmox node with Nix installed,
  flakes enabled, and existing proxnix runtime files present.

### Phase 2: Manifest Contract

- Define the flake output contract.
- Add sample `proxnix.nodes.<node>` manifest.
- Add a command to evaluate and print the node manifest.

Concrete tasks:

- Add generated authority wrapper under `/var/lib/proxnix/authority`.
- Render per-CT `proxmox.nix` from `/etc/pve/lxc/<vmid>.conf` using existing
  `proxnix-host pve-conf-to-nix`.
- Generate a node manifest from existing `/var/lib/proxnix/containers`.
- Keep the first schema small: `vmid`, `hostname`, `systemAttr`, `system`,
  `sourceRevision`, and normalized `pve`.

Acceptance:

- `nix eval --json /var/lib/proxnix/authority#proxnix.nodes.<node>` prints all
  locally managed VMIDs.
- The manifest contains the same VMIDs as `/var/lib/proxnix/containers/*` for
  numeric directories with matching `/etc/pve/lxc/<vmid>.conf`.

### Phase 3: Dry-Run Reconciler

- Implement `proxnix-reconcile --dry-run`.
- Pull/update authority.
- Evaluate manifest.
- Print planned CT actions.

Acceptance:

- No CT is started, stopped, created, or modified.
- No Nix build is performed unless an explicit build flag is added.
- The command exits nonzero on invalid manifest JSON or missing required tools.
- `--vmid <id>` limits output to one CT.

### Phase 4: Build One CT

- Build one CT system closure on the Proxmox host.
- Record the desired system path in status.
- Do not activate yet.

Concrete tasks:

- Add `proxnix-reconcile --build-only --vmid <id>` or equivalent internal phase
  guard.
- Store `desiredSystem` and build status in
  `/var/lib/proxnix/status/<vmid>.json`.
- Do not require the CT to be running.

Acceptance:

- Host `nix build` succeeds for one CT.
- Status has `lastBuildStatus=ok`, `desiredSystem`, and `lastDeployStatus` still
  set to `not-run` or the prior deploy state.

### Phase 5: Seed Closure

- Export the host-built closure.
- Import it into the target CT through `pct exec`.
- Verify the desired system path exists inside the guest.

Acceptance:

- The CT does not need external network access.
- `pct exec <vmid> -- test -x <system>/bin/switch-to-configuration` succeeds.
- A failed import records `lastDeployStatus=failed` with `lastError`.

### Phase 6: Activate One CT

- Run `switch-to-configuration` by exact system path.
- Verify `/run/current-system`.
- Record status.

Acceptance:

- `pct exec <vmid> -- readlink -f /run/current-system` equals
  `desiredSystem`.
- A second `proxnix-reconcile --vmid <id>` is a no-op after manifest evaluation.
- `previousSystem` is populated on the second successful distinct activation.

### Phase 7: Recreate One CT

- Destroy or remove a test CT.
- Recreate it from manifest and local authority.
- Seed closure and activate.

Concrete tasks:

- Refactor `proxnix-create-lxc` so reconciler can call it non-interactively from
  manifest data.
- Make missing CT creation explicit with `--recreate-missing`.
- Do not implement automatic replacement of existing CTs in this phase.

Acceptance:

- With `/etc/pve/lxc/<vmid>.conf` absent and manifest lifecycle data present,
  `proxnix-reconcile --vmid <id> --recreate-missing` creates, starts, seeds, and
  activates the CT.

### Phase 8: Timer And Workstation Trigger

- Add `proxnix-reconcile.service`.
- Add `proxnix-reconcile.timer`.
- Add workstation command wrappers for deploy and status.

Concrete tasks:

- Add `proxnix publish --reconcile`.
- Add `proxnix deploy` as CLI sugar for publish plus reconcile.
- Add host-side `proxnix-reconcile --status`.
- Add workstation JSON handling for remote reconcile/status output.

Acceptance:

- `proxnix deploy --host root@pve1 --vmid 101` publishes the selected CT data and
  runs `proxnix-reconcile --vmid 101`.
- `systemctl start proxnix-reconcile.service` uses the same code path.

### Phase 9: Remove Guest Rebuild Path

Remove the primary guest-side rebuild mechanism:

- channel bootstrap in the guest runner
- boot-time `nixos-rebuild switch`
- `current-config-hash` / `applied-config-hash` as activation authority
- tmpfs rendered config as the deployment trigger

Keep only guest files that are required for activation, diagnostics, or secrets.

Acceptance:

- A fresh CT no longer receives `proxnix-apply-config.service`.
- No normal path runs `nixos-rebuild switch` inside the guest.
- Existing docs and doctor output no longer describe config hashes as the
  convergence source of truth.

### Phase 10: Hardening

- Add per-CT locks.
- Add rollback.
- Add health checks.
- Add signed manifest or signed commit verification.
- Add Prometheus/status export if needed.

## First End-To-End Milestone

The pivot is real when this works for one CT without guest networking or guest
evaluation:

```bash
proxnix-reconcile --vmid 101
```

Expected behavior:

1. Host evaluates the authority repo.
2. Host builds the desired NixOS system closure.
3. Host ensures the CT exists.
4. Host imports the closure into the CT.
5. Host activates the exact system path.
6. Host verifies `/run/current-system`.
7. Host writes status.

At that point, proxnix has moved from guest-side self-rebuilds to node-side
reconciliation.
