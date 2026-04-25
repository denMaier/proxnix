# LXC Exercise Lab

`proxnix-lxc-exercise` provisions a disposable-but-real proxnix exercise lab on
one Proxmox host and writes a report you can inspect afterward.

It generates an isolated workstation site repo, publishes it through the normal
relay-cache path, creates one proxnix-managed NixOS LXC, waits for the first
boot apply to finish, and then checks the feature surfaces that proxnix owns.

## What it covers

- `proxnix-create-lxc`
- full publish plus `--report-changes`
- site doctor and remote drift doctor
- host doctor
- `containers/_template/` imports
- top-level `dropins/*.nix`
- attached `dropins/*.sh`
- Nix-managed guest systemd units declared from `dropins/*.nix`
- shared, grouped, and container-local secrets
- activation-lifetime secret files and templates
- service-lifetime secret files and templates
- `createOnly` secret-backed config seeds
- secret oneshot consumers
- Podman secret-driver integration inside a managed guest
- targeted `--config-only --vmid <vmid>` dry-run publish reporting

## Output

By default the command writes under:

```text
.codex-staging/lxc-exercise/
```

The latest report lands at:

```text
.codex-staging/lxc-exercise/reports/latest/report.md
.codex-staging/lxc-exercise/reports/latest/report.json
```

The Markdown report is the human summary. The JSON report is suitable for
automation. `reports/latest/artifacts/` contains raw stdout/stderr logs for the
captured publish, doctor, and remote provisioning commands.

The guest itself also publishes a status page once the assertion services run:

```text
http://<guest-ip>:18080/status.json
http://<guest-ip>:18080/
```

That document is the canonical end-to-end signal for the guest-side feature
surfaces. It covers activation-time secret materialization, service-lifetime
secret handling, Podman secret integration, and the final managed config hash.

## Usage

Minimal invocation:

```bash
workstation/cli/bin/proxnix-lxc-exercise --host root@node1 --base-vmid 940
```

Useful overrides:

```bash
workstation/cli/bin/proxnix-lxc-exercise \
  --host root@node1 \
  --base-vmid 950 \
  --template local:vztmpl/nixos-25.11-x86_64-linux.tar.xz \
  --storage local-lvm \
  --timeout-seconds 5400
```

If a previous exercise attempt failed and left its VMIDs behind, rerun with:

```bash
workstation/cli/bin/proxnix-lxc-exercise --host root@node1 --base-vmid 940 --cleanup-existing
```

Static networking example:

```bash
workstation/cli/bin/proxnix-lxc-exercise \
  --host root@node1 \
  --base-vmid 950 \
  --cleanup-existing \
  --template local:vztmpl/nixos-25.11-x86_64-linux.tar.xz \
  --ip 192.168.178.240/24 \
  --gw 192.168.178.1 \
  --nameserver 192.168.178.100
```

## Notes

- The chosen base VMID must be unused. The command fails fast if it already
  exists, unless `--cleanup-existing` is set and the existing container already
  matches the expected `proxnix-exercise-*` hostname.
- NixOS exercise containers are created with `nesting=1,keyctl=1`.
- The synthetic secrets are test values only, but they still move through the
  real SOPS and relay-cache flow.
- The generated site is isolated from your normal proxnix site repo by using a
  dedicated generated config file in the exercise work dir.
- The host-side Markdown/JSON report is only as good as the harness control
  path. When debugging harness behavior, prefer the guest-published
  `status.json` and the per-command logs under `reports/latest/artifacts/`.
