#!/bin/bash
# uninstall.sh — Remove proxnix from a Proxmox node.
#
# Removes the per-node files installed by install.sh.
# /var/lib/proxnix/ is intentionally left untouched — it holds node-local
# container config, pubkeys, and encrypted secrets that the operator manages.
#
# Must be run as root on the Proxmox host.
#
# Usage:
#   ./uninstall.sh [--dry-run]

set -euo pipefail

LXC_CONFIG_DIR="/usr/share/lxc/config"
LXC_HOOKS_DIR="/usr/share/lxc/hooks"
PROXNIX_LIB_DIR="/usr/local/lib/proxnix"
PROXNIX_SBIN_DIR="/usr/local/sbin"
SYSTEMD_UNIT_DIR="/etc/systemd/system"

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

do_rmdir_if_empty() {
    local dir="$1"
    if [[ ! -d "$dir" ]]; then
        return
    fi
    if [[ $DRY_RUN -eq 1 ]]; then
        log "[dry-run] rmdir $dir (if empty)"
        return
    fi
    rmdir "$dir" 2>/dev/null || true
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

action "Lifecycle hooks"
do_rm "$LXC_HOOKS_DIR/nixos-proxnix-prestart"
do_rm "$LXC_HOOKS_DIR/nixos-proxnix-mount"

action "Local runtime helper"
do_rm "$PROXNIX_LIB_DIR/yaml-to-nix.py"
do_rm "$PROXNIX_LIB_DIR/nixos-proxnix-common.sh"
do_rmdir_if_empty "$PROXNIX_LIB_DIR"

action "Local admin helper"
do_rm "$PROXNIX_SBIN_DIR/proxnix-doctor"
do_rm "$PROXNIX_SBIN_DIR/bootstrap-guest-secrets.sh"
do_rm "$PROXNIX_SBIN_DIR/proxnix-create-lxc"

action "GC timer"
if [[ $DRY_RUN -eq 0 ]]; then
    systemctl disable --now proxnix-gc.timer 2>/dev/null || true
fi
do_rm "$SYSTEMD_UNIT_DIR/proxnix-gc.timer"
do_rm "$SYSTEMD_UNIT_DIR/proxnix-gc.service"
if [[ $DRY_RUN -eq 0 ]]; then
    systemctl daemon-reload
fi

echo ""
echo "Done."
echo ""
echo "  /var/lib/proxnix/ was not touched."
echo "  Container configs, public keys, and encrypted secrets are still intact."
echo ""
echo "  To fully remove proxnix data from this node, delete:"
echo "    rm -rf /var/lib/proxnix"
