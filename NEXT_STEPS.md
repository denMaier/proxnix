# Next Steps

## Current state

- The LXC exercise harness no longer defaults to three separate guests.
- `workstation/src/proxnix_workstation/exercise_cli.py` now builds a single
  kitchen-sink exercise CT at the selected `--base-vmid`.
- The exercise harness also now supports an optional `--nameserver` override so
  temporary static-IP runs can inject DNS before first boot.
- That single CT covers:
  - template imports
  - attached scripts and attached systemd units
  - shared, group, and container-local secrets
  - activation secret files and templates
  - service-lifetime secret files and templates
  - `createOnly` templates
  - oneshot secret consumers
  - Podman secret-driver integration
  - nginx-served `status.json` / `index.html` reporting
- The host runtime fixes are implemented and deployed on `node1`
  (`192.168.178.101`):
  - attached scripts staged under `runtime/bin` now remain executable
  - optional secret oneshots now skip cleanly when the fetched secret is
    missing or empty
  - `/run/proxnix` is now prepared with mode `0711` so the mount hook can
    traverse into staged per-VM state on fresh boots
- Local verification passed:
  - `workstation/.venv/bin/python -m unittest discover -s workstation/tests -p 'test_*.py'`
  - `python3 -m py_compile ...`
  - `bash -n host/lxc/hooks/nixos-proxnix-prestart`

## What is still broken

- The remaining blocker is not the three-CT fixture anymore.
- Fresh exercise CT boots are still intermittently failing before proxnix can
  apply the managed config.
- The observed failure mode is:
  - CT starts with `ip=dhcp`
  - `eth0` gets carrier and IPv6 link-local only
  - no IPv4 address appears
  - no default route appears
  - `systemd-networkd` stays in `configuring`
  - `proxnix-apply-config` fails while trying to bootstrap root channels from
    `https://nixos.org/channels/...`
- This has now reproduced on both:
  - the old three-CT exercise runs
  - the new single-CT run at VMID `950`
- A temporary static-IP run on April 18, 2026 with:
  - `--ip 192.168.178.240/24`
  - `--gw 192.168.178.1`
  - `--nameserver 192.168.178.100`
  got past channel bootstrap and completed `proxnix-apply-config`, so the
  original blocker is still first-boot guest DHCP/DNS.
- That static-IP run exposed new post-apply failures:
  - `proxnix-exercise-service-reader.service` loops because
    `/run/proxnix-exercise/service-secret.txt` never appears
  - `proxnix-exercise-baseline-status.service` fails with
    `PermissionError: [Errno 13] Permission denied: '/usr/local/bin/proxnix-baseline-report.sh'`
    even though the file shows mode `555`
  - nginx can listen on `:18080`, but `/status.json` returns HTTP `500`
    because the baseline status writer never completed

## Important findings

- A manual recovery inside `940` using a static IP let `nixos-rebuild switch`
  progress far enough to expose two real proxnix bugs:
  - attached script execution failed because staged scripts lost execute bits
  - `optional = true` oneshot secret handling still failed on an empty fetched
    secret
- Both of those source bugs are now fixed.
- The current end-to-end failure is therefore upstream of those fixes: guest
  IPv4/DHCP is not reliably available on first boot.
- Existing non-NixOS DHCP CTs on `node1` still obtain working `vmbr0` leases,
  which weakens the “router broke DHCP globally” hypothesis.
- During the failing DHCP reproductions, the disposable NixOS guest rendered
  the expected `/etc/systemd/network/eth0.network` with `DHCP = ipv4`, but
  `systemd-networkd` still emitted no DHCP traffic on `vmbr0`.
- The static-IP run proved the rest of the bootstrap path can progress:
  `proxnix-apply-config` completed and the Podman exercise check passed.

## Start here next time

1. Treat guest IPv4 acquisition as the primary blocker, not the exercise
   fixture.
2. Reproduce on a fresh VMID, preferably one CT at a time.
3. Inspect on the Proxmox host and inside the guest:
   - `pct config <vmid>`
   - `pct exec <vmid> -- /run/current-system/sw/bin/ip -4 addr show`
   - `pct exec <vmid> -- /run/current-system/sw/bin/ip route`
   - `pct exec <vmid> -- /run/current-system/sw/bin/networkctl status eth0`
   - `pct exec <vmid> -- /run/current-system/sw/bin/journalctl -u systemd-networkd -u systemd-resolved -b`
4. Determine whether the real issue is:
   - upstream DHCP flakiness on the LAN/router
   - `vmbr0` / bridge forwarding behavior on `node1`
   - a first-boot race where proxnix starts before guest IPv4 is actually
     usable
5. Keep the static-IP workaround available so non-network regressions can still
   be tested while DHCP remains unresolved:
   - `workstation/bin/proxnix-lxc-exercise --host root@192.168.178.101 --base-vmid 950 --cleanup-existing --template mooseFS:vztmpl/nixos-image-lxc-proxmox-25.11pre-git-x86_64-linux.tar.xz --ip 192.168.178.240/24 --gw 192.168.178.1 --nameserver 192.168.178.100`
6. Follow up the new post-apply failures found under that workaround:
   - why `/usr/local/bin/proxnix-baseline-report.sh` is still not executable to
     the Python status writer despite mode `555`
   - why service-lifetime exercise files never materialize under
     `/run/proxnix-exercise/`
   - why exercise services introduced by the switched config remain inactive
     until manually started
7. Only after networking is stable, rerun the single-CT exercise and confirm
   that:
   - `proxnix-apply-config` completes
   - nginx serves `/status.json`
   - the attached-script and optional-secret fixes behave correctly end-to-end

## Useful commands

Run the exercise on a fresh VMID:

```bash
workstation/bin/proxnix-lxc-exercise --host root@192.168.178.101 --base-vmid 950 --cleanup-existing
```

Run the temporary static-IP workaround:

```bash
workstation/bin/proxnix-lxc-exercise \
  --host root@192.168.178.101 \
  --base-vmid 950 \
  --cleanup-existing \
  --template mooseFS:vztmpl/nixos-image-lxc-proxmox-25.11pre-git-x86_64-linux.tar.xz \
  --ip 192.168.178.240/24 \
  --gw 192.168.178.1 \
  --nameserver 192.168.178.100
```

Redeploy the host runtime to `node1` after host-side changes:

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/.ansible/tmp \
workstation/.venv/bin/ansible-playbook \
  -i host/inventory.proxmox.ini \
  host/ansible/install.yml \
  -l node1
```

Inspect the latest exercise report:

```text
.codex-staging/lxc-exercise/reports/latest/report.md
.codex-staging/lxc-exercise/reports/latest/report.json
```

## Relevant files

- Harness: `workstation/src/proxnix_workstation/exercise_cli.py`
- SSH session handling: `workstation/src/proxnix_workstation/ssh_ops.py`
- Host helper: `host/proxnix-create-lxc`
- Prestart hook: `host/lxc/hooks/nixos-proxnix-prestart`
- Shared guest module: `host/common.nix`
- Exercise docs: `docs/operations/lxc-exercise-lab.md`
- Command reference: `docs/reference/commands.md`
