# Command Reference

## Host commands

### `host/deploy/ansible/install.yml`

Install proxnix onto one or more Proxmox nodes from a control machine over SSH.
This is the only supported host deployment path. It verifies Proxmox and Nix,
enables flakes for an existing Nix installation, stages the host flake source
under `/var/lib/proxnix/install-source`, installs or upgrades
`/nix/var/nix/profiles/proxnix-host`, and runs `proxnix-host-activate`. The
activation command links the Nix-profile payload into the mutable Proxmox paths
that LXC and systemd expect. It is not meant to run against `localhost`. By
default Nix must already be installed. To install Nix when missing, explicitly
set `proxnix_nix_install_mode=determinate`; this uses the Determinate Systems
installer.

```bash
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml -e proxnix_target_hosts=proxmox_cluster
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/install.yml -e proxnix_nix_install_mode=determinate
```

### `host/deploy/ansible/ai-agent-bootstrap.yml`

Install proxnix onto one or more Proxmox nodes, verify `proxnix-doctor --host-only`,
render a workstation config, and optionally run the disposable exercise harness,
without publishing a live site repo.

```bash
cp host/deploy/ansible/ai-agent-bootstrap.vars.example.yml host/deploy/ansible/ai-agent-bootstrap.vars.yml
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/ai-agent-bootstrap.yml -e @host/deploy/ansible/ai-agent-bootstrap.vars.yml
```

### `host/deploy/ansible/ai-agent-deploy.yml`

End-to-end agent playbook for install + workstation config + site validation +
publish + optional exercise.

```bash
cp host/deploy/ansible/ai-agent-deploy.vars.example.yml host/deploy/ansible/ai-agent-deploy.vars.yml
ansible-playbook -i host/deploy/inventory.proxmox.ini host/deploy/ansible/ai-agent-deploy.yml -e @host/deploy/ansible/ai-agent-deploy.vars.yml
```

### `ci/install-git-hooks.sh`

Install the repo-managed git hooks:

```bash
./ci/install-git-hooks.sh
```

This configures:

```text
core.hooksPath = .githooks
```

### `ci/install-workstation.sh`

Install or upgrade the workstation Python package:

```bash
./ci/install-workstation.sh
./ci/install-workstation.sh --version 1.2.3
```

For the normal end-user install, prefer:

```bash
pip install proxnix-workstation
```

That installs both:

- `proxnix`
- `proxnix-tui`

### `ci/render-homebrew-cask.sh`

Render the Homebrew tap cask for Proxnix Manager:

```bash
./ci/render-homebrew-cask.sh --version 0.1.0
./ci/render-homebrew-cask.sh --version 0.1.0 --sha256-arm64 <sha256>
./ci/render-homebrew-cask.sh --version 0.1.0 --output ../homebrew-tap/Casks/proxnix-manager.rb
```

### `ci/render-homebrew-formula.sh`

Render the Homebrew tap formula for the workstation CLI and TUI:

```bash
./ci/render-homebrew-formula.sh --version 0.1.0
./ci/render-homebrew-formula.sh --version 0.1.0 --sha256 <sha256>
./ci/render-homebrew-formula.sh --version 0.1.0 --output ../homebrew-tap/Formula/proxnix-workstation.rb
```

### `ci/bootstrap-workstation-venv.sh`

Create or reuse `workstation/.venv`, install `ansible`, and install the current
repo version of `proxnix-workstation` into that repo-local virtualenv.

```bash
./ci/bootstrap-workstation-venv.sh
```

For local Proxnix Manager work with the `pykeepass` provider, install
`pykeepass` after bootstrapping:

```bash
workstation/.venv/bin/python -m pip install pykeepass
```

If a Manager-only integration needs extra import paths, set
`PROXNIX_MANAGER_PYTHONPATH` through the app settings or in
`~/.config/proxnix/config`.

### `ci/release.sh`

One-command release flow:

```bash
./ci/release.sh patch
./ci/release.sh minor
./ci/release.sh major --no-push
./ci/release.sh --version 1.2.3-rc1
```

This reads the current version from `VERSION`, bumps one numeric component when
asked, updates `workstation/cli/pyproject.toml`, creates a release commit, creates
an annotated `v*` tag, and pushes by default.

### `ci/bump-version.sh`

Update the version files without committing or tagging:

```bash
./ci/bump-version.sh patch
./ci/bump-version.sh minor
./ci/bump-version.sh major
```

### `ci/set-version.sh`

Update the canonical project version files without tagging:

```bash
./ci/set-version.sh 1.2.3
```

### `ci/release-tag.sh`

Create an annotated release tag and optionally push it:

```bash
./ci/release-tag.sh 1.2.3
./ci/release-tag.sh 1.2.3 --push
./ci/release-tag.sh 1.2.3-rc1 --push
```

This expects the tag version to match both `VERSION` and
`workstation/cli/pyproject.toml`.

### `proxnix-host`

Rust host controller for migrated host-side behavior. Current subcommands
include:

```bash
proxnix-host pve-conf-to-nix --pve-conf /etc/pve/lxc/101.conf --out-dir /tmp/out
proxnix-host authority render
proxnix-host hook prestart --vmid 101
proxnix-host hook mount --vmid 101 --rootfs /path/to/rootfs
proxnix-host hook poststop --vmid 101
proxnix-host reconcile podman-secrets --rootfs /path/to/rootfs --vmid 101 --secrets-dir /path/to/secrets
proxnix-host state init
```

### `proxnix-host-activate`

Activate the Nix-installed proxnix host profile on a Proxmox node. It creates
the host integration symlinks for LXC hooks, helper commands, host tools,
shared Nix modules, and systemd units, then reloads systemd and enables the GC
and flake-update timers.

This command is normally called by `host/deploy/ansible/install.yml`.

### `proxnix-host-uninstall`

Remove proxnix's installed assets from the current Proxmox node. Leaves
`/var/lib/proxnix` intact and removes the proxnix host profile.

This command is installed onto the host by `host/deploy/ansible/install.yml`, so
you do not need to keep the original repo checkout around just to uninstall
proxnix.

`proxnix-uninstall` is kept as a compatibility alias.

### `host/uninstall.sh`

Repo-local source for the same uninstall logic shipped as
`proxnix-host-uninstall`.

### `proxnix-doctor <vmid>`

Run host and per-container health checks.

```bash
proxnix-doctor 100
proxnix-doctor --all
proxnix-doctor --host-only
```

Host-only checks include the Nix daemon, `nix-command`/flakes support, `/nix`
free space, the authority and status directories, and the reconciler command
plus explicit/event-triggered service units.

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | Warnings found, no hard failures |
| 2 | One or more hard failures |

Sample output for a healthy relay-backed container:

```text
[ct 100]
  OK    PVE config present: /etc/pve/lxc/100.conf
  OK    ostype=nixos
  INFO  state: running
  OK    guest file present: /var/lib/proxnix/build-input/configuration.nix
  OK    host relay encrypted container identity present: /var/lib/proxnix/private/containers/100/age_identity.sops.yaml
  OK    guest container age identity present
  INFO  legacy managed config hash is informational because reconciler status exists
```

### `proxnix-reconcile`

Host-side reconciler entrypoint. The phase-1 command installs status plumbing
and host prerequisite validation. Dry-run renders and evaluates the generated
authority manifest, then prints planned actions without building or modifying
containers. Managed CTs are cluster-scoped and can float between nodes; each
node only acts on CTs that are local according to Proxmox cluster placement,
falling back to `pct status <vmid>` when the cluster view is unavailable.
Non-local targets are reported as `skip not-local`.

`--build-only --vmid <id>` builds one selected local system closure and
writes `/var/lib/proxnix/status/<vmid>.json` without activating it. If the
recorded current system already equals the evaluated desired system, it exits
as `noop-current` without running `nix build`. `--seed-only --vmid <id>`
copies the recorded desired closure into a running target CT through a
temporary host Unix socket bridge to the CT's Nix daemon and verifies
`switch-to-configuration` exists. `--activate-only --vmid <id>` activates the
recorded desired system in a running CT and verifies `/run/current-system`.
`--vmid <id>` is the orchestration command: evaluate desired path, skip as
`noop-current` if `/run/current-system` already matches, otherwise build, seed,
activate, verify, and write status. `--recreate-missing` creates a CT only when
manifest placement explicitly targets the current node.

The phase commands are also exposed as separate host commands:

- `proxnix-reconcile-build-golden` (`proxnix-host reconcile build-golden`)
- `proxnix-reconcile-build --vmid <id>`
- `proxnix-reconcile-seed --vmid <id>` (`proxnix-host reconcile seed`)
- `proxnix-reconcile-seed-offline --vmid <id> --rootfs <mounted-rootfs>` (`proxnix-host reconcile seed-offline`)
- `proxnix-reconcile-activate --vmid <id>`

Use those commands when you want to drive build, seed, and activation separately.
`proxnix-reconcile --vmid <id>` remains the command that runs all three phases
for a running CT.

The running-CT seed path creates `/run/proxnix/ct-<vmid>.sock` for the
duration of the reconcile run and points the host-side Nix client at it with
`NIX_REMOTE=unix:///run/proxnix/ct-<vmid>.sock`. The bridge connects to the
container-local Nix daemon socket, normally
`/nix/var/nix/daemon-socket/socket`, through `nsenter`, `lxc-attach`, or
`pct exec`. It does not run a custom in-container proxnix agent.

Normal convergence is not run by a full-host timer. For a stopped CT start,
the LXC pre-start hook opportunistically warms the host-local golden-template
build with `proxnix-reconcile-build-golden` and then runs
`proxnix-reconcile-build --vmid <id>`. The mount hook runs
`proxnix-reconcile-seed-offline --vmid <id> --rootfs <mounted-rootfs>`,
which copies the closure into the mounted rootfs and advances the rootfs NixOS
system profile so the CT boots the desired system directly. It also writes
`next-system` as a compatibility marker for older activation flows. Explicit
operator/workstation flows can still run
`proxnix-reconcile --vmid <id>` or start `proxnix-reconcile@<id>.service` for a
running CT. `proxnix-reconcile.service` remains available for an explicit
all-local-container run, but no `proxnix-reconcile.timer` is installed or
enabled.

```bash
proxnix-reconcile --dry-run
proxnix-reconcile --dry-run --vmid 100
proxnix-reconcile-build-golden
proxnix-reconcile --build-only --vmid 100
proxnix-reconcile --seed-only --vmid 100
proxnix-reconcile --activate-only --vmid 100
proxnix-reconcile --vmid 100
proxnix-reconcile --vmid 100 --recreate-missing
proxnix-reconcile --rollback --vmid 100
proxnix-reconcile --status
proxnix-reconcile --status --vmid 100
proxnix-reconcile-build --vmid 100
proxnix-reconcile-seed --vmid 100
proxnix-reconcile-seed-offline --vmid 100 --rootfs /run/lxc/100/rootfs
proxnix-reconcile-activate --vmid 100
systemctl start proxnix-reconcile@100.service
```

Status JSON keeps compatibility fields such as `desiredSystem` and
`currentSystem`, and also includes descriptive fields such as `desired_system`,
`current_system`, `container_is_local`, `host_has_closure`,
`container_has_closure`, and `protected_by_host_gc_root`. Common status names
include `noop-current`, `build-failed`, `lost-locality`, `failed`, and `ok`.

### `proxnix-gc`

Prune host-side transient state without deleting useful local build cache roots.
This installed command is a compatibility wrapper for `proxnix-host gc`.
The command removes copied pre-start stage directories under `/run/proxnix/`,
keeps `/var/lib/proxnix/gcroots/deploy/golden-template`, keeps
`<vmid>-desired` roots for CTs still present on this host, and removes
`<vmid>-desired` roots for CTs that are no longer local/present.

```bash
proxnix-gc --dry-run
systemctl start proxnix-gc.service
```

On Proxmox hosts, do not run `nix-collect-garbage` against the host store for
normal proxnix cleanup. `proxnix-gc` understands the deployment GC roots that
protect desired closures; direct host store collection should be reserved for
manual recovery after checking those roots.

### `proxnix-flake-update`

Update the host-managed authority flake lock. This installed command is a
compatibility wrapper for `proxnix-host flake-update`. It renders
`/var/lib/proxnix/authority`, runs `nix flake update --flake
/var/lib/proxnix/authority`, copies the resulting
`/var/lib/proxnix/authority/flake.lock` back to `/var/lib/proxnix/flake.lock`,
and records the last successful update under
`/var/lib/proxnix/state/flake-update.last-success`.

The installed `proxnix-flake-update.timer` runs daily. The command gates actual
updates with `PROXNIX_FLAKE_UPDATE_FREQUENCY`, so the same timer can provide
daily, weekly, or monthly updates. Configure it in
`/etc/proxnix/flake-update.conf`:

```bash
PROXNIX_FLAKE_UPDATE_FREQUENCY=weekly
# PROXNIX_FLAKE_UPDATE_INPUTS=nixpkgs
```

Supported frequencies are `daily`, `weekly`, `monthly`, and `disabled`.
`PROXNIX_FLAKE_UPDATE_INPUTS` is optional and is passed to `nix flake update`
to update only selected inputs. The command shares the reconciler global lock,
so it does not race a build or activation run.

```bash
proxnix-flake-update --force
proxnix-flake-update --frequency monthly
systemctl start proxnix-flake-update.service
systemctl edit proxnix-flake-update.service
```

Updating the lock only changes future desired closures. A stopped CT picks up
the new inputs on its next pre-start build. A running CT picks them up when an
operator or workstation flow runs `proxnix-reconcile --vmid <id>`.

### `proxnix-authority-render`

Render the compatibility authority wrapper under `/var/lib/proxnix/authority`.
The wrapper exposes cluster-level `proxnix.containers` and a node view at
`proxnix.nodes.<node>`. Local `/etc/pve/lxc/*.conf` data is used when available
for generated Proxmox metadata, but runtime locality is decided by the
reconciler from Proxmox cluster placement with `pct status` as fallback.

```bash
proxnix-authority-render
proxnix-authority-render --print-manifest
proxnix-authority-render --node-name pve1
```

### `proxnix-create-lxc`

Create a NixOS LXC on a Proxmox host that is ready for proxnix management.

This helper:

- checks the existing proxnix install by calling `proxnix-doctor --host-only`
- auto-detects the newest local NixOS template when `--template` is omitted
- auto-detects a rootdir-capable storage when `--storage` is omitted
- creates the CT with `ostype=nixos`
- always sets Proxmox CT features `nesting=1,keyctl=1` for NixOS guests
- starts the CT by default after creating it
- optionally creates `/var/lib/proxnix/containers/<vmid>/dropins`
- supports `--no-doctor` for reconciler-controlled non-interactive creation
- supports `--cleanup-existing` for safe reruns when that VMID already belongs
  to a container whose hostname already matches `--hostname`
- never attempts to install proxnix itself
- does not generate secret identities on the host

## Workstation commands

### `proxnix`

Unified workstation entrypoint for the workstation-authoritative proxnix flows.

```bash
proxnix config show
proxnix deploy --host root@node1 --vmid 100
proxnix deploy-status root@node1 --vmid 100
proxnix secrets ls 120
proxnix publish --vmid 120
proxnix doctor --site-only
proxnix tui
proxnix exercise lxc --host root@node1 --base-vmid 940
```

Preferred verb layout:

- `proxnix config show` — print resolved workstation config
- `proxnix config plan-tree` — show the publish plan tree for all containers
- `proxnix secrets ...` — manage secrets (see below)
- `proxnix publish ...` — publish site to relay hosts
- `proxnix doctor ...` — run health checks
- `proxnix tui` (alias: `proxnix ui`) — open the terminal UI
- `proxnix exercise lxc ...` — run the exercise lab

The legacy split commands such as `proxnix-secrets`, `proxnix-publish`,
`proxnix-doctor`, `proxnix-tui`, and `proxnix-lxc-exercise` remain available as
compatibility aliases.

### `workstation/cli/bin/proxnix-tui`

Terminal UI for the workstation-side proxnix workflows.

It reads the same `~/.config/proxnix/config` file as `proxnix-publish` and
`proxnix-secrets`, scans the configured site repo for containers, and wraps the
common publish and secret-management actions in a curses interface.

```bash
workstation/cli/bin/proxnix-tui
proxnix-tui
```

The packaged and Nix-installed variants place `proxnix-tui` on `PATH`.

Current coverage includes:

- publish all or one VMID with `--dry-run`, `--config-only`, and `--report-changes`
- list, get, set, remove, rotate, and initialize proxnix secrets
- per-container actions such as publish, config-only publish, and identity init
- captured command output for reviewing script results inside the TUI

### `proxnix-secrets`

This is the workstation-authoritative helper for the external proxnix site repo.

**Configuration:** `~/.config/proxnix/config` (see [installation step 3](../getting-started/installation.md#step-3-configure-your-workstation))

Source-secret retrieval is controlled by `PROXNIX_SECRET_PROVIDER`. Runtime
publish artifacts remain SOPS-based regardless of provider.

### Listing

```bash
proxnix-secrets ls
proxnix-secrets ls <vmid>
proxnix-secrets ls-shared
proxnix-secrets ls-group <group>
```

### Reading

```bash
proxnix-secrets get <vmid> <name>
proxnix-secrets get-shared <name>
proxnix-secrets get-group <group> <name>
```

### Writing

```bash
proxnix-secrets set <vmid> <name>
proxnix-secrets set-shared <name>
proxnix-secrets set-group <group> <name>
```

Both commands prompt interactively for the secret value. You can also pipe a value:

```bash
printf %s "myvalue" | proxnix-secrets set 120 db_password
```

### Removing

```bash
proxnix-secrets rm <vmid> <name>
proxnix-secrets rm-shared <name>
proxnix-secrets rm-group <group> <name>
```

### Rotating recipients

```bash
proxnix-secrets rotate <vmid>
proxnix-secrets rotate-shared
proxnix-secrets rotate-group <group>
```

These rotation commands are only available with the `embedded-sops` provider.

### Identity and store initialization

```bash
proxnix-secrets init-host-relay
proxnix-secrets init-container 120
proxnix-secrets init-shared
```

`set` creates guest identities automatically when needed. `init-host-relay`
creates the shared relay key that Proxmox hosts use to decrypt guest identities
during staging. `init-shared` creates the shared secret store.

Built-in provider names:

- `embedded-sops`
- `pass`
- `gopass`
- `passhole`
- `pykeepass`
- `onepassword`
- `onepassword-cli`
- `keepassxc`
- `bitwarden`
- `bitwarden-cli`
- `exec`

### `proxnix-publish`

Publish the workstation-owned site repo to one or more Proxmox relay hosts.

```bash
proxnix-publish
proxnix-publish root@node1
proxnix-publish --dry-run
proxnix-publish --config-only
proxnix-publish --vmid 100
proxnix-publish --config-only --vmid 100
```

It pushes config and per-container runtime secret stores into
`/var/lib/proxnix/private/containers/<vmid>/`, stores the shared plaintext
host relay key at `/etc/proxnix/host_relay_identity`, and stores container
identities re-encrypted to both the host relay key and the master recovery key
under `/var/lib/proxnix/private/containers/<vmid>/`.

When the site directory is a git worktree, publish uses the committed `HEAD`
snapshot rather than the live worktree. It writes the deployed revision to
`/var/lib/proxnix/publish-revision.json` on the host. If staged, unstaged, or
untracked local changes exist, publish prints a warning because those changes
are ignored.

If the site has a committed `flake.lock`, publish also copies it to
`/var/lib/proxnix/flake.lock`. The host authority renderer then carries that
lock into `/var/lib/proxnix/authority/flake.lock`, making the golden-template
build and every CT build use the same locked Nix inputs. If no local lock is
published, publish leaves any host-managed `/var/lib/proxnix/flake.lock` in
place. The host can advance that durable lock with `proxnix-flake-update` and
its timer.

Use `--config-only` to sync only `site.nix` and `containers/`, skipping all secret stores and identities.

Use `--vmid <vmid>` to sync only `/var/lib/proxnix/containers/<vmid>/` plus the shared `/var/lib/proxnix/containers/_template/` tree and, unless `--config-only` is also set, `/var/lib/proxnix/private/containers/<vmid>/`.

`--config-only --vmid <vmid>` syncs only `/var/lib/proxnix/containers/<vmid>/` plus `/var/lib/proxnix/containers/_template/`.

`--container-config <vmid>` remains as a compatibility alias for `--config-only --vmid <vmid>`.

### `workstation/cli/bin/proxnix-doctor`

Lint the workstation-owned site repo and optionally compare the expected relay
cache against one or more Proxmox hosts over SSH.

```bash
workstation/cli/bin/proxnix-doctor
workstation/cli/bin/proxnix-doctor --site-only
workstation/cli/bin/proxnix-doctor --vmid 120
workstation/cli/bin/proxnix-doctor root@node1
workstation/cli/bin/proxnix-doctor --config-only root@node1
```

It checks:

- local config and workstation prerequisites
- secret store payloads decrypt and contain only flat string keys
- encrypted identity stores decrypt and yield SSH public keys
- `secret-groups.list` syntax and referenced group-store presence
- compiled publish tree generation for config and secret payloads
- remote relay-cache drift over SSH using the same publish scope as `proxnix-publish`

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | Warnings found, no hard failures |
| 2 | One or more hard failures |

### `workstation/cli/bin/proxnix-lxc-exercise`

Create and validate a dedicated proxnix exercise lab on one Proxmox host.

```bash
workstation/cli/bin/proxnix-lxc-exercise --host root@node1 --base-vmid 940
workstation/cli/bin/proxnix-lxc-exercise --host root@node1 --base-vmid 950 --template local:vztmpl/nixos.tar.xz --storage local-lvm
workstation/cli/bin/proxnix-lxc-exercise --host root@node1 --base-vmid 940 --cleanup-existing
workstation/cli/bin/proxnix-lxc-exercise --host root@node1 --base-vmid 950 --cleanup-existing --ip 192.168.178.240/24 --gw 192.168.178.1 --nameserver 192.168.178.100
```

It:

- generates an isolated workstation-side site repo under `.codex-staging/lxc-exercise/`
- seeds synthetic shared, grouped, and container-local secrets through the normal SOPS flow
- publishes that site through the normal relay-cache path
- creates one proxnix-managed NixOS LXC with a combined exercise workload
- waits for the first boot apply to finish
- runs host, workstation, and in-guest assertions
- writes Markdown and JSON reports plus raw command logs

The guest also publishes its own status document at `http://<guest-ip>:18080/status.json`.
When debugging exercise behavior, treat that guest-published status page and the
captured `reports/latest/artifacts/` logs as the primary truth.

If an earlier exercise run left those VMIDs behind, rerun with
`--cleanup-existing`. The harness only destroys pre-existing containers when the
current VMIDs already match its expected `proxnix-exercise-*` hostnames; any
other hostname still hard-fails.

The latest report lands under:

```text
.codex-staging/lxc-exercise/reports/latest/
```

## Guest commands

### `proxnix-help`

Print a short live summary inside the guest, including VMID, IP, memory, disk, config status, and useful follow-up commands.

### `proxnix-secrets ls`

List visible secret names inside the guest.

### `proxnix-secrets get <name>`

Read a decrypted secret value from the guest.

### Useful Podman commands

```bash
podman ps -a
podman logs -f <name>
podman auto-update --dry-run
systemctl status podman-<name>.service
```

### Useful NixOS commands

```bash
nixos-rebuild switch
nixos-rebuild list-generations
nix-collect-garbage -d
```
