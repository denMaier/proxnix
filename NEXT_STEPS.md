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
- The earlier DHCP diagnosis was incorrect:
  - another container on the host was interfering with networking
  - DHCP itself is working again

## What is still broken

- A temporary static-IP run on April 18, 2026 with:
  - `--ip 192.168.178.240/24`
  - `--gw 192.168.178.1`
  - `--nameserver 192.168.178.100`
  got past channel bootstrap and completed `proxnix-apply-config`.
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
- The later DHCP investigation turned out to be environmental noise rather than
  a proxnix guest-config regression: another container was interfering with
  host networking.
- The static-IP run proved the rest of the bootstrap path can progress:
  `proxnix-apply-config` completed and the Podman exercise check passed.

## Start here next time

1. Reproduce on a fresh VMID, preferably one CT at a time.
2. If networking looks broken again during exercise runs, check for another
   container interfering with host networking before blaming guest DHCP.
3. Follow up the post-apply failures found under the static-IP debugging run:
   - why `/usr/local/bin/proxnix-baseline-report.sh` is still not executable to
     the Python status writer despite mode `555`
   - why service-lifetime exercise files never materialize under
     `/run/proxnix-exercise/`
   - why exercise services introduced by the switched config remain inactive
     until manually started
4. Rerun the single-CT exercise and confirm that:
   - `proxnix-apply-config` completes
   - nginx serves `/status.json`
   - the attached-script and optional-secret fixes behave correctly end-to-end

## Useful commands

Run the exercise on a fresh VMID:

```bash
workstation/bin/proxnix-lxc-exercise --host root@192.168.178.101 --base-vmid 950 --cleanup-existing
```

Run the temporary static-IP workaround when isolating non-network regressions:

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
