#!/bin/bash
# bootstrap-guest-secrets.sh — Legacy helper for storing a container SSH public
# key used as an age recipient for a NixOS LXC container.
#
# New proxnix containers use host-generated per-container keys, so this script
# is no longer needed during the normal creation flow. It remains available as a
# repair helper for legacy containers or manual imports.
#
# Usage:
#   ./bootstrap-guest-secrets.sh <vmid>
#
# After running, manage secrets with:
#   proxnix-secrets set <vmid> mysecret

set -euo pipefail

VMID="${1:?Usage: $0 <vmid>}"
NIXLXC_DIR="/etc/pve/proxnix"
CONTAINER_DIR="${NIXLXC_DIR}/containers/${VMID}"
MASTER_PUBKEY_FILE="${NIXLXC_DIR}/master_age_pubkey"
GUEST_PUBKEY_FILE="/etc/proxnix/secrets/identity.pub"
NIXOS_CURRENT_SYSTEM_BIN="/run/current-system/sw/bin"

if [[ ! -d "$CONTAINER_DIR" ]]; then
  echo "ERROR: ${CONTAINER_DIR} does not exist."
  echo "       Create it and add proxmox.yaml / user.yaml first."
  exit 1
fi

if [[ -f "${CONTAINER_DIR}/age_pubkey" ]]; then
  PUBKEY="$(cat "${CONTAINER_DIR}/age_pubkey" | tr -d '\r\n')"
  echo "Container ${VMID} already has a host-managed SSH age public key:"
  echo "  ${PUBKEY}"
  echo "Stored at: ${CONTAINER_DIR}/age_pubkey"
  echo ""
elif ! PUBKEY="$(pct exec "$VMID" -- "${NIXOS_CURRENT_SYSTEM_BIN}/cat" "$GUEST_PUBKEY_FILE" 2>/dev/null | tr -d '\r\n')"; then
  echo "ERROR: ${GUEST_PUBKEY_FILE} not found inside container ${VMID}."
  echo "       For new containers, proxnix now creates host-managed keys automatically."
  echo "       This legacy helper is only needed for older/manual containers."
  exit 1
fi

if [[ -z "$PUBKEY" ]]; then
  echo "ERROR: Could not read public key from container ${VMID}."
  exit 1
fi

printf '%s\n' "$PUBKEY" > "${CONTAINER_DIR}/age_pubkey"

echo "Container ${VMID} SSH age public key:"
echo "  ${PUBKEY}"
echo "Stored at: ${CONTAINER_DIR}/age_pubkey"
echo ""

if [[ -f "$MASTER_PUBKEY_FILE" ]]; then
  MASTER_PUBKEY="$(cat "$MASTER_PUBKEY_FILE")"
  echo "Master public key (${MASTER_PUBKEY_FILE}):"
  echo "  ${MASTER_PUBKEY}"
  echo ""
else
  echo "NOTE: No master public key found at ${MASTER_PUBKEY_FILE}"
  echo "      Create that file with your SSH public key to enable"
  echo "      master-key recovery and multi-recipient encryption."
  echo ""
fi

echo "Create or update a secret for this container:"
echo "  proxnix-secrets set ${VMID} mysecret"
echo ""
echo "Then restart the container to stage the SOPS YAML store and register"
echo "the Podman shell-driver secrets (the pre-start hook runs automatically)."
echo ""
echo "Shared secrets are available automatically in all containers once proxnix-secrets init-shared has been run."
