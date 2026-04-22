from __future__ import annotations

import textwrap


def render_nspawn_backend_shell() -> str:
    return textwrap.dedent(
        """\
        local_nixos_container_cleanup_mounts() {
          if [ ! -d "${ROOTFS}" ]; then
            return
          fi

          systemd-nspawn --cleanup -D "${ROOTFS}" -M "${GUEST_MACHINE}" >/dev/null 2>&1 || true

          while IFS= read -r mountpoint; do
            [ -n "$mountpoint" ] || continue
            umount "$mountpoint" >/dev/null 2>&1 || true
          done < <(
            mount \
              | awk -v root="${ROOTFS}" '$3 ~ ("^" root "(/|$)") { print length($3) " " $3 }' \
              | sort -rn \
              | cut -d" " -f2-
          )
        }

        local_nixos_container_clear_immutable() {
          if [ ! -d "${ROOTFS}" ]; then
            return
          fi
          if command -v chattr >/dev/null 2>&1; then
            chattr -R -i "${ROOTFS}" >/dev/null 2>&1 || true
          fi
        }

        local_nixos_container_reset_rootfs() {
          local_nixos_container_cleanup_mounts
          local_nixos_container_clear_immutable
          rm -rf "${ROOTFS}"
        }

        local_nixos_container_prepare_runtime_tree() {
          # systemd-nspawn expects to own /dev, /proc, and /run. After a
          # bootstrap activation NixOS may leave /dev populated with device
          # nodes, which causes nspawn startup to fail.
          rm -rf "${ROOTFS}/dev" "${ROOTFS}/proc" "${ROOTFS}/run"
          mkdir -p "${ROOTFS}/boot" "${ROOTFS}/dev" "${ROOTFS}/proc" "${ROOTFS}/run"
          chmod 0755 "${ROOTFS}/boot" "${ROOTFS}/dev" "${ROOTFS}/proc" "${ROOTFS}/run"
        }

        local_nixos_container_stop() {
          machinectl terminate "${GUEST_MACHINE}" >/dev/null 2>&1 || true
          systemd-nspawn --cleanup -D "${ROOTFS}" -M "${GUEST_MACHINE}" >/dev/null 2>&1 || true
          local_nixos_container_cleanup_mounts
        }

        local_nixos_container_start() {
          local_nixos_container_stop
          systemd-nspawn \
            -D "${ROOTFS}" \
            -M "${GUEST_MACHINE}" \
            --register=yes \
            /nix/var/nix/profiles/system/init >"${NSPAWN_LOG}" 2>&1 &
        }

        local_nixos_container_wait_until_ready() {
          local ready=0
          for _ in $(seq 1 "${TIMEOUT_SECONDS}"); do
            if systemd-run -M "${GUEST_MACHINE}" --wait --pipe --quiet /nix/var/nix/profiles/system/sw/bin/bash -lc 'true' >/dev/null 2>&1; then
              ready=1
              break
            fi
            sleep 1
          done
          if [ "${ready}" -ne 1 ]; then
            echo "--- nspawn log ---" >&2
            tail -n 200 "${NSPAWN_LOG}" >&2 || true
            return 1
          fi
        }
        """
    )


def render_prestart_stage_apply_shell() -> str:
    return textwrap.dedent(
        """\
        local_nixos_container_register_podman_secrets() {
          local proxnix_secret_dir="${1}"
          local vmid="${2}"

          python3 - "${ROOTFS}" "${vmid}" "${proxnix_secret_dir}" <<'PYEOF'
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

rootfs = Path(sys.argv[1])
vmid = sys.argv[2]
secrets_dir = Path(sys.argv[3])


def top_level_keys(path: Path):
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text().splitlines():
        if not line or line[0].isspace() or ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if key and key != "sops":
            keys.add(key)
    return keys


live_names = set()
if secrets_dir.is_dir():
    live_names.update(top_level_keys(secrets_dir / "effective.sops.yaml"))

secrets_json = rootfs / "var/lib/containers/storage/secrets/secrets.json"
ids_dir = rootfs / "etc/secrets/.ids"
label_key = "proxnix.managed"

driver_opts = {
    "store": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman store",
    "lookup": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman lookup",
    "list": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman list",
    "delete": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman delete",
}


def make_uuid(vmid: str, name: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"proxnix:{vmid}:{name}").hex


ids_dir.mkdir(parents=True, exist_ok=True)
secrets_json.parent.mkdir(parents=True, exist_ok=True)
secrets_json.parent.chmod(0o700)

if secrets_json.exists():
    try:
        data = json.loads(secrets_json.read_text())
    except Exception:
        data = {}
else:
    data = {}

if not isinstance(data.get("secrets"), dict):
    data["secrets"] = {}
if not isinstance(data.get("nameToID"), dict):
    data["nameToID"] = {}
if not isinstance(data.get("idToName"), dict):
    data["idToName"] = {}

now = datetime.now(timezone.utc).isoformat()
changed = False

stale_ids = [
    sid
    for sid, entry in data["secrets"].items()
    if isinstance(entry.get("labels"), dict)
    and entry["labels"].get(label_key) == "true"
    and (entry.get("name") or "") not in live_names
]
for sid in stale_ids:
    name = data["secrets"][sid].get("name", "")
    del data["secrets"][sid]
    data["nameToID"].pop(name, None)
    data["idToName"].pop(sid, None)
    (ids_dir / sid).unlink(missing_ok=True)
    changed = True

for name in sorted(live_names):
    sid = make_uuid(vmid, name)
    existing = data["secrets"].get(sid, {})
    created_at = existing.get("createdAt") or now

    entry = {
        "name": name,
        "id": sid,
        "labels": {label_key: "true"},
        "metadata": {},
        "createdAt": created_at,
        "updatedAt": now,
        "driver": "shell",
        "driverOptions": driver_opts,
    }
    if data["secrets"].get(sid) != entry:
        data["secrets"][sid] = entry
        data["nameToID"][name] = sid
        data["idToName"][sid] = name
        changed = True

    ids_file = ids_dir / sid
    if not ids_file.exists() or ids_file.read_text() != name:
        ids_file.write_text(name, encoding="utf-8")
        ids_file.chmod(0o640)
        changed = True

if changed:
    secrets_json.write_text(json.dumps(data, indent=2) + "\\n", encoding="utf-8")
    secrets_json.chmod(0o600)
PYEOF
        }

        local_nixos_container_apply_prestart_stage() {
          local stage_dir="${1}"
          local bind_config_dir="${stage_dir}/bind/config"
          local bind_runtime_dir="${stage_dir}/bind/runtime"
          local bind_secrets_dir="${stage_dir}/bind/secrets"
          local copy_runtime_dir="${stage_dir}/copy/runtime"
          local copy_runtime_bin_dir="${copy_runtime_dir}/bin"
          local copy_etc_nixos_dir="${stage_dir}/copy/etc/nixos"
          local copy_etc_systemd_attached_dir="${stage_dir}/copy/etc/systemd/system.attached"

          local proxnix_state_dir="${ROOTFS}/var/lib/proxnix"
          local proxnix_config_dir="${proxnix_state_dir}/config"
          local proxnix_runtime_dir="${proxnix_state_dir}/runtime"
          local proxnix_runtime_bin_dir="${proxnix_runtime_dir}/bin"
          local proxnix_runtime_manifest_dir="${proxnix_runtime_dir}/manifests"
          local proxnix_secret_dir="${proxnix_state_dir}/secrets"
          local attached_dir="${ROOTFS}/etc/systemd/system.attached"
          local attached_wants_dir="${attached_dir}/multi-user.target.wants"

          if [ ! -f "${copy_etc_nixos_dir}/configuration.nix" ] || [ ! -d "${bind_config_dir}/managed" ] || [ ! -f "${bind_runtime_dir}/current-config-hash" ] || [ ! -f "${bind_runtime_dir}/vmid" ] || [ ! -f "${copy_runtime_dir}/proxnix-apply-config-runner" ] || [ ! -f "${copy_etc_systemd_attached_dir}/proxnix-apply-config.service" ]; then
            echo "prestart stage is incomplete at ${stage_dir}" >&2
            return 1
          fi

          mkdir -p \
            "${ROOTFS}/etc/nixos" \
            "${proxnix_config_dir}" \
            "${proxnix_runtime_bin_dir}" \
            "${proxnix_runtime_manifest_dir}" \
            "${proxnix_secret_dir}" \
            "${ROOTFS}/etc/secrets" \
            "${attached_wants_dir}"
          chmod 700 "${ROOTFS}/etc/secrets" "${proxnix_secret_dir}"

          install -m 0644 "${copy_etc_nixos_dir}/configuration.nix" "${ROOTFS}/etc/nixos/configuration.nix"

          rm -rf "${proxnix_config_dir}"
          mkdir -p "${proxnix_config_dir}"
          cp -a "${bind_config_dir}/." "${proxnix_config_dir}/"

          install -m 0400 "${bind_runtime_dir}/current-config-hash" "${proxnix_runtime_dir}/current-config-hash"
          install -m 0400 "${bind_runtime_dir}/vmid" "${proxnix_runtime_dir}/vmid"

          rm -rf "${proxnix_runtime_bin_dir}"
          mkdir -p "${proxnix_runtime_bin_dir}"
          if [ -d "${copy_runtime_bin_dir}" ]; then
            cp -a "${copy_runtime_bin_dir}/." "${proxnix_runtime_bin_dir}/"
          fi
          chmod 0555 "${proxnix_runtime_bin_dir}"/* 2>/dev/null || true
          install -m 0555 "${copy_runtime_dir}/proxnix-apply-config-runner" "${proxnix_runtime_dir}/proxnix-apply-config-runner"

          rm -rf "${proxnix_secret_dir}"
          mkdir -p "${proxnix_secret_dir}"
          if [ -d "${bind_secrets_dir}" ]; then
            cp -a "${bind_secrets_dir}/." "${proxnix_secret_dir}/"
            chmod 0600 "${proxnix_secret_dir}"/* 2>/dev/null || true
          fi
          local_nixos_container_register_podman_secrets "${proxnix_secret_dir}" "$(cat "${bind_runtime_dir}/vmid")"

          rm -f "${ROOTFS}/etc/systemd/system/proxnix-apply-config.service"
          rm -f "${ROOTFS}/etc/systemd/system/multi-user.target.wants/proxnix-apply-config.service"
          install -m 0644 "${copy_etc_systemd_attached_dir}/proxnix-apply-config.service" "${attached_dir}/proxnix-apply-config.service"
          ln -sfn ../proxnix-apply-config.service "${attached_wants_dir}/proxnix-apply-config.service"
        }
        """
    )
