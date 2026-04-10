#!/bin/bash
# bootstrap.sh — Extract and store the age public key for a NixOS LXC container.
#
# The age keypair is generated idempotently by the base.nix activation script
# on first boot, so the private key never transits through the Proxmox host.
# This script merely reads the public key out of the running container and
# stores it on the host so you can use it to encrypt secrets.
#
# Run this once after the container has booted with base.nix applied.
#
# Usage:
#   ./bootstrap.sh <vmid>
#
# After running, encrypt secrets with:
#   printf 'mysecretvalue' | age \
#     -r "$(cat /etc/pve/proxnix/containers/<vmid>/age_pubkey)" \
#     -r "$(cat /etc/pve/proxnix/master_age_pubkey)" \
#     -o /etc/pve/priv/proxnix/containers/<vmid>/secrets/mysecret.age

set -euo pipefail

VMID="${1:?Usage: $0 <vmid>}"
NIXLXC_DIR="/etc/pve/proxnix"
NIXLXC_PRIV_DIR="/etc/pve/priv/proxnix"
CONTAINER_DIR="${NIXLXC_DIR}/containers/${VMID}"
MASTER_PUBKEY_FILE="${NIXLXC_DIR}/master_age_pubkey"

if [[ ! -d "$CONTAINER_DIR" ]]; then
  echo "ERROR: ${CONTAINER_DIR} does not exist."
  echo "       Create it and add proxmox.yaml / user.yaml first."
  exit 1
fi

# Verify the keypair exists (created by base.nix activation script on first boot)
if ! pct exec "$VMID" -- test -f /etc/age/identity.txt 2>/dev/null; then
  echo "ERROR: /etc/age/identity.txt not found inside container ${VMID}."
  echo "       Make sure the container has booted at least once with base.nix"
  echo "       applied (the activation script generates the keypair on first boot)."
  exit 1
fi

# Extract the public key from the identity file comment
PUBKEY="$(pct exec "$VMID" -- sh -c \
  "grep '^# public key:' /etc/age/identity.txt | awk '{print \$NF}'")"

if [[ -z "$PUBKEY" ]]; then
  echo "ERROR: Could not read public key from container ${VMID}."
  exit 1
fi

# Store on host for use in encryption commands
mkdir -p "${NIXLXC_PRIV_DIR}/containers/${VMID}/secrets"
echo "$PUBKEY" > "${CONTAINER_DIR}/age_pubkey"

echo "Container ${VMID} age public key:"
echo "  ${PUBKEY}"
echo "Stored at: ${CONTAINER_DIR}/age_pubkey"
echo ""

# Build the encryption command
RECIPIENTS=("-r" "${PUBKEY}")
if [[ -f "$MASTER_PUBKEY_FILE" ]]; then
  MASTER_PUBKEY="$(cat "$MASTER_PUBKEY_FILE")"
  RECIPIENTS+=("-r" "${MASTER_PUBKEY}")
  echo "Master public key (${MASTER_PUBKEY_FILE}):"
  echo "  ${MASTER_PUBKEY}"
  echo ""
else
  echo "NOTE: No master public key found at ${MASTER_PUBKEY_FILE}"
  echo "      Create that file with your age or SSH public key to enable"
  echo "      master-key recovery and multi-recipient encryption."
  echo ""
fi

echo "Encrypt a secret for this container:"
RECIP_FLAGS=""
for r in "${RECIPIENTS[@]}"; do
  [[ "$r" != "-r" ]] && RECIP_FLAGS+=" -r '${r}'" || true
done
echo "  printf 'mysecretvalue' | age${RECIP_FLAGS} \\"
echo "    -o ${NIXLXC_PRIV_DIR}/containers/${VMID}/secrets/mysecret.age"
echo ""
echo "Then restart the container to push the encrypted file and register"
echo "the Podman shell-driver secret (the pre-start hook runs automatically)."
echo ""
echo "If this container should access shared secrets, mark it:"
echo "  touch ${CONTAINER_DIR}/shared_secrets"
echo "  The shared private key will be deployed automatically on next container start."
