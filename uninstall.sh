#!/bin/bash
# uninstall.sh — Remove proxnix from a Proxmox node.
#
# Removes the per-node files installed by install.sh.
# /etc/pve/proxnix/ is intentionally left untouched — it holds container
# configs and secrets that are shared across the cluster via pmxcfs.
#
# Must be run as root on the Proxmox host.
#
# Usage:
#   ./uninstall.sh [--dry-run]

set -euo pipefail

LXC_CONFIG_DIR="/usr/share/lxc/config"
LXC_HOOKS_DIR="/usr/share/lxc/hooks"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

die()    { echo "ERROR: $*" >&2; exit 1; }
log()    { echo "  $*"; }
action() { echo ""; echo "→ $*"; }

do_rm() {
    local path="$1"
    if [[ ! -e "$path" ]]; then
        log "(already absent) $path"
        return
    fi
    if [[ $DRY_RUN -eq 1 ]]; then
        log "[dry-run] rm $path"
        return
    fi
    rm -f "$path"
    log "removed: $path"
}

[[ "$(id -u)" -eq 0 ]] || die "Must be run as root."
command -v pveversion >/dev/null 2>&1 || die "pveversion not found — is this a Proxmox host?"

echo ""
echo "proxnix uninstall"
echo "================="
[[ $DRY_RUN -eq 1 ]] && echo "(dry run — no files will be removed)"

action "LXC config files"
do_rm "$LXC_CONFIG_DIR/nixos.common.conf"
do_rm "$LXC_CONFIG_DIR/nixos.userns.conf"

action "Pre-start hook"
do_rm "$LXC_HOOKS_DIR/nixos-proxnix-prestart"

echo ""
echo "Done."
echo ""
echo "  /etc/pve/proxnix/ was not touched."
echo "  Container configs, secrets, and age keys are still intact."
echo ""
echo "  To fully remove proxnix from the cluster, delete that directory"
echo "  manually on one node (pmxcfs will replicate the deletion):"
echo "    rm -rf /etc/pve/proxnix"
