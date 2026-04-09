#!/bin/bash
# Proxmox hookscript for NixOS LXC containers.
#
# Install:
#   cp hookscript.sh /var/lib/vz/snippets/nixos-hookscript.sh
#   chmod +x /var/lib/vz/snippets/nixos-hookscript.sh
#   pct set <vmid> -hookscript local:snippets/nixos-hookscript.sh
#
# Host layout:
#   /etc/nixos-lxc/
#   ├── master_age_pubkey
#   ├── yaml-to-nix.py
#   └── containers/<vmid>/
#       ├── proxmox.yaml
#       ├── user.yaml
#       ├── age_pubkey             (written by bootstrap.sh)
#       ├── secrets/*.age          (encrypted with container key + master key)
#       └── dropins/
#           ├── extra.nix          → /etc/nixos/dropins/
#           ├── myapp.container    → /etc/containers/systemd/
#           ├── mynet.network      → /etc/containers/systemd/
#           └── mydata.volume      → /etc/containers/systemd/
#
# Phase split:
#   pre-start  — container is STOPPED; use pct mount for all file writes
#   post-start — container is RUNNING; use pct exec for commands

set -euo pipefail

VMID="$1"
PHASE="$2"

NIXLXC_DIR="/etc/nixos-lxc"
CONTAINER_DIR="${NIXLXC_DIR}/containers/${VMID}"
YAML_TO_NIX="${NIXLXC_DIR}/yaml-to-nix.py"
WORKDIR="/tmp/nixos-lxc-${VMID}"
ROOTFS=""

log() { echo "[nixos-hook][${VMID}][${PHASE}] $*"; }

# Always unmount and clean up on exit from pre-start
cleanup_mount() {
  if [[ -n "$ROOTFS" ]]; then
    pct unmount "$VMID" 2>/dev/null || true
    ROOTFS=""
  fi
  rm -rf "$WORKDIR"
}

case "$PHASE" in

  # ── pre-start ──────────────────────────────────────────────────────────────
  # Container is STOPPED. pct push does NOT work here.
  # Use pct mount to get direct access to the rootfs.
  pre-start)
    if [[ ! -d "$CONTAINER_DIR" ]]; then
      log "No config directory at ${CONTAINER_DIR}, skipping."
      exit 0
    fi

    trap cleanup_mount EXIT
    mkdir -p "$WORKDIR"

    # Generate proxmox.nix and user.nix from YAML
    ARGS=("--out-dir" "$WORKDIR")
    [[ -f "${CONTAINER_DIR}/proxmox.yaml" ]] && ARGS+=("--proxmox-yaml" "${CONTAINER_DIR}/proxmox.yaml")
    [[ -f "${CONTAINER_DIR}/user.yaml"    ]] && ARGS+=("--user-yaml"    "${CONTAINER_DIR}/user.yaml")
    log "Generating .nix files from YAML..."
    python3 "$YAML_TO_NIX" "${ARGS[@]}"

    # Mount the container rootfs; pct mount prints the path in single quotes
    ROOTFS=$(pct mount "$VMID" | awk -F"'" '{print $2}')
    if [[ -z "$ROOTFS" || ! -d "$ROOTFS" ]]; then
      log "ERROR: pct mount did not return a valid path (got: ${ROOTFS})"
      exit 1
    fi
    log "Mounted rootfs at ${ROOTFS}"

    # Ensure target directories exist in the rootfs
    mkdir -p "${ROOTFS}/etc/nixos" \
             "${ROOTFS}/etc/nixos/dropins" \
             "${ROOTFS}/etc/secrets" \
             "${ROOTFS}/etc/containers/systemd"
    chmod 700 "${ROOTFS}/etc/secrets"

    # Write generated .nix files
    for f in proxmox.nix user.nix; do
      if [[ -f "${WORKDIR}/${f}" ]]; then
        cp "${WORKDIR}/${f}" "${ROOTFS}/etc/nixos/${f}"
        chmod 644 "${ROOTFS}/etc/nixos/${f}"
        log "Wrote ${f}"
      fi
    done

    # Copy encrypted secret files (stay encrypted at rest)
    SECRETS_DIR="${CONTAINER_DIR}/secrets"
    if [[ -d "$SECRETS_DIR" ]]; then
      while IFS= read -r -d '' f; do
        fname="$(basename "$f")"
        cp "$f" "${ROOTFS}/etc/secrets/${fname}"
        chmod 400 "${ROOTFS}/etc/secrets/${fname}"
        log "Wrote encrypted secret: ${fname}"
      done < <(find "$SECRETS_DIR" -maxdepth 1 -name '*.age' -type f -print0 2>/dev/null)
    fi

    # Copy drop-in files
    DROPIN_DIR="${CONTAINER_DIR}/dropins"
    if [[ -d "$DROPIN_DIR" ]]; then
      while IFS= read -r -d '' f; do
        fname="$(basename "$f")"
        case "$fname" in
          *.nix)
            cp "$f" "${ROOTFS}/etc/nixos/dropins/${fname}"
            chmod 644 "${ROOTFS}/etc/nixos/dropins/${fname}"
            log "Wrote .nix drop-in: ${fname}"
            ;;
          *.container|*.volume|*.network|*.pod|*.image|*.build)
            cp "$f" "${ROOTFS}/etc/containers/systemd/${fname}"
            chmod 644 "${ROOTFS}/etc/containers/systemd/${fname}"
            log "Wrote Quadlet drop-in: ${fname}"
            ;;
          *)
            log "Ignored unknown drop-in type: ${fname}"
            ;;
        esac
      done < <(find "$DROPIN_DIR" -maxdepth 1 -type f -print0 2>/dev/null)
    fi

    # cleanup_mount runs via trap on EXIT
    ;;

  # ── post-start ─────────────────────────────────────────────────────────────
  # Container is RUNNING. All file writes are done; this phase runs commands
  # that require a live container.
  post-start)
    if [[ ! -d "$CONTAINER_DIR" ]]; then
      exit 0
    fi

    # ── Podman shell-driver secret registration ────────────────────────────
    # The global shell driver is configured in base.nix via containers.conf.d.
    # We pass the secret name as stdin so the store command can write the
    # UUID→name mapping that lookup uses to find the right .age file.
    SECRETS_DIR="${CONTAINER_DIR}/secrets"
    if [[ -d "$SECRETS_DIR" ]]; then
      while IFS= read -r -d '' f; do
        secret_name="$(basename "${f%.age}")"

        # Remove any stale registration, then re-create.
        # printf pipes the name as stdin; the global store command writes
        # /etc/secrets/.ids/<uuid> = name so lookup can resolve it.
        pct exec "$VMID" -- sh -c \
          "podman secret rm '${secret_name}' 2>/dev/null; \
           printf '%s' '${secret_name}' | podman secret create '${secret_name}' -" \
          2>&1 | sed "s/^/[nixos-hook][${VMID}] /" || {
            log "WARNING: Could not register Podman secret '${secret_name}'"
          }
        log "Registered Podman secret (age shell driver): ${secret_name}"
      done < <(find "$SECRETS_DIR" -maxdepth 1 -name '*.age' -type f -print0 2>/dev/null)
    fi

    # ── nixos-rebuild ──────────────────────────────────────────────────────
    log "Triggering nixos-rebuild switch..."
    pct exec "$VMID" -- /run/current-system/sw/bin/nixos-rebuild switch \
      2>&1 | sed "s/^/[nixos-hook][${VMID}] /" || {
        log "WARNING: nixos-rebuild exited non-zero; check the container journal."
      }

    # ── Quadlet drop-ins: reload + start ──────────────────────────────────
    DROPIN_DIR="${CONTAINER_DIR}/dropins"
    if [[ -d "$DROPIN_DIR" ]]; then
      QUADLET_SERVICES=()
      while IFS= read -r -d '' f; do
        fname="$(basename "$f")"
        case "$fname" in
          *.container|*.volume|*.network|*.pod|*.image|*.build)
            QUADLET_SERVICES+=("${fname%.*}.service")
            ;;
        esac
      done < <(find "$DROPIN_DIR" -maxdepth 1 -type f -print0 2>/dev/null)

      if [[ ${#QUADLET_SERVICES[@]} -gt 0 ]]; then
        log "Reloading systemd for Quadlet drop-ins..."
        pct exec "$VMID" -- systemctl daemon-reload
        for svc in "${QUADLET_SERVICES[@]}"; do
          pct exec "$VMID" -- systemctl start "$svc" \
            && log "Started ${svc}" \
            || log "WARNING: Could not start ${svc}"
        done
      fi
    fi
    ;;

  *)
    exit 0
    ;;

esac

exit 0
