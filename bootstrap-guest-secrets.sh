#!/bin/bash
# bootstrap-guest-secrets.sh — Extract and store the guest SSH public key used
# as an age recipient for a NixOS LXC container.
#
# The SSH keypair is generated idempotently by the base.nix activation script
# on first boot, so the private key never transits through the Proxmox host.
# This script merely reads the public key out of the running container and
# stores it on the host so you can use it to encrypt secrets.
#
# Run this once after the container has booted with base.nix applied.
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

if ! PUBKEY="$(pct exec "$VMID" -- "${NIXOS_CURRENT_SYSTEM_BIN}/cat" "$GUEST_PUBKEY_FILE" 2>/dev/null | tr -d '\r\n')"; then
  echo "ERROR: ${GUEST_PUBKEY_FILE} not found inside container ${VMID}."
  echo "       Make sure the container has booted at least once with base.nix"
  echo "       applied (the activation script generates the keypair on first boot)."
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
