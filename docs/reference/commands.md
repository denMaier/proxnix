# Command Reference

## Host commands

### `host/install.sh`

Install proxnix onto the current Proxmox node.
After it finishes, the repo checkout is no longer required on that node for
normal use or uninstall.

Useful flags:

| Flag | Purpose |
|------|---------|
| `--dry-run` | Preview what would be installed without writing anything |
| `--force-shared` | Deprecated compatibility flag; ignored in node-local mode |

### `host/ansible/install.yml`

Install proxnix onto one or more Proxmox nodes from a control machine over SSH.
It copies files from this repo on the Ansible controller to the remote hosts in
your inventory; it is not meant to run against `localhost`.

```bash
ansible-playbook -i host/inventory.proxmox.ini host/ansible/install.yml
ansible-playbook -i host/inventory.proxmox.ini host/ansible/install.yml -e proxnix_target_hosts=proxmox_cluster
```

### `host/ansible/ai-agent-bootstrap.yml`

Install proxnix onto one or more Proxmox nodes, verify `proxnix-doctor --host-only`,
render a workstation config, and optionally run the disposable exercise harness,
without publishing a live site repo.

```bash
cp host/ansible/ai-agent-bootstrap.vars.example.yml host/ansible/ai-agent-bootstrap.vars.yml
ansible-playbook -i host/inventory.proxmox.ini host/ansible/ai-agent-bootstrap.yml -e @host/ansible/ai-agent-bootstrap.vars.yml
```

### `host/ansible/ai-agent-deploy.yml`

End-to-end agent playbook for install + workstation config + site validation +
publish + optional exercise.

```bash
cp host/ansible/ai-agent-deploy.vars.example.yml host/ansible/ai-agent-deploy.vars.yml
ansible-playbook -i host/inventory.proxmox.ini host/ansible/ai-agent-deploy.yml -e @host/ansible/ai-agent-deploy.vars.yml
```

### `host/remote/github-install.sh`

Curl-friendly wrapper for `install.sh`.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/github-install.sh)"
bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/github-install.sh)" -- --dry-run
```

### `host/remote/install-host-package.sh`

Canonical host helper-script install. It resolves and installs the published
`proxnix-host` Debian package for the current node architecture:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/install-host-package.sh)"
bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/install-host-package.sh)" -- --version 0.1.0
```

### `host/packaging/package-deb.sh`

Build the Debian host package:

```bash
./host/packaging/package-deb.sh
```

Artifact output:

```text
dist/proxnix-host_<version>_<arch>.deb
```

Install the resulting package on a Proxmox node with:

```bash
apt install ./dist/proxnix-host_<version>_<arch>.deb
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
asked, updates `workstation/pyproject.toml`, creates a release commit, creates
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
`workstation/pyproject.toml`.

### `proxnix-uninstall`

Remove proxnix's installed assets from the current Proxmox node. Leaves
`/var/lib/proxnix` intact.

This command is installed onto the host by `host/install.sh` and
`host/ansible/install.yml`, so you do not need to keep the original repo
checkout around just to uninstall proxnix.

If the node was installed from the Debian package instead, remove it with:

```bash
apt remove proxnix-host
```

### `host/uninstall.sh`

Repo-local source for the same uninstall logic shipped as `proxnix-uninstall`.

### `proxnix-doctor <vmid>`

Run host and per-container health checks.

```bash
proxnix-doctor 100
proxnix-doctor --all
proxnix-doctor --host-only
```

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
  OK    guest file present: /etc/nixos/configuration.nix
  OK    host relay encrypted container identity present: /var/lib/proxnix/private/containers/100/age_identity.sops.yaml
  OK    guest container age identity present
  OK    applied managed config hash matches current hash
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
- supports `--cleanup-existing` for safe reruns when that VMID already belongs
  to a container whose hostname already matches `--hostname`
- never attempts to install proxnix itself
- does not generate secret identities on the host

## Workstation commands

### `proxnix`

Unified workstation entrypoint for the workstation-authoritative proxnix flows.

```bash
proxnix config show
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

### `workstation/bin/proxnix-tui`

Terminal UI for the workstation-side proxnix workflows.

It reads the same `~/.config/proxnix/config` file as `proxnix-publish` and
`proxnix-secrets`, scans the configured site repo for containers, and wraps the
common publish and secret-management actions in a curses interface.

```bash
workstation/bin/proxnix-tui
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

Use `--config-only` to sync only `site.nix` and `containers/`, skipping all secret stores and identities.

Use `--vmid <vmid>` to sync only `/var/lib/proxnix/containers/<vmid>/` plus the shared `/var/lib/proxnix/containers/_template/` tree and, unless `--config-only` is also set, `/var/lib/proxnix/private/containers/<vmid>/`.

`--config-only --vmid <vmid>` syncs only `/var/lib/proxnix/containers/<vmid>/` plus `/var/lib/proxnix/containers/_template/`.

`--container-config <vmid>` remains as a compatibility alias for `--config-only --vmid <vmid>`.

### `workstation/bin/proxnix-doctor`

Lint the workstation-owned site repo and optionally compare the expected relay
cache against one or more Proxmox hosts over SSH.

```bash
workstation/bin/proxnix-doctor
workstation/bin/proxnix-doctor --site-only
workstation/bin/proxnix-doctor --vmid 120
workstation/bin/proxnix-doctor root@node1
workstation/bin/proxnix-doctor --config-only root@node1
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

### `workstation/bin/proxnix-lxc-exercise`

Create and validate a dedicated proxnix exercise lab on one Proxmox host.

```bash
workstation/bin/proxnix-lxc-exercise --host root@node1 --base-vmid 940
workstation/bin/proxnix-lxc-exercise --host root@node1 --base-vmid 950 --template local:vztmpl/nixos.tar.xz --storage local-lvm
workstation/bin/proxnix-lxc-exercise --host root@node1 --base-vmid 940 --cleanup-existing
workstation/bin/proxnix-lxc-exercise --host root@node1 --base-vmid 950 --cleanup-existing --ip 192.168.178.240/24 --gw 192.168.178.1 --nameserver 192.168.178.100
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
