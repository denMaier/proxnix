#!/bin/bash
# uninstall.sh — Remove proxnix from a Proxmox node.
#
# Removes the per-node files installed by the Ansible playbook. This script is also
# installed onto the host as /usr/local/sbin/proxnix-uninstall so the original
# repo checkout is not required for normal uninstall operations.
# /var/lib/proxnix/ and /etc/proxnix/ are intentionally left untouched — they
# hold relay-cache config and secret material that the operator publishes from
# the workstation.
#
# Must be run as root on the Proxmox host.
#
# Usage:
#   proxnix-uninstall [--dry-run]

set -euo pipefail

LXC_CONFIG_DIR="/usr/share/lxc/config"
LXC_HOOKS_DIR="/usr/share/lxc/hooks"
PROXNIX_LIB_DIR="/usr/local/lib/proxnix"
PROXNIX_SBIN_DIR="/usr/local/sbin"
PROXNIX_LOCAL_BIN_DIR="/usr/local/bin"
PROXNIX_DATA_DIR="/var/lib/proxnix"
PROXNIX_DEPLOY_GCROOT_DIR="${PROXNIX_DATA_DIR}/gcroots/deploy"
PROXNIX_HOST_PROFILE="/nix/var/nix/profiles/proxnix-host"
PROXNIX_LEGACY_HOST_TOOLS_PROFILE="/nix/var/nix/profiles/proxnix-host-tools"
SYSTEMD_UNIT_DIR="/etc/systemd/system"
PROXNIX_INSTALL_MANIFEST="${PROXNIX_LIB_DIR}/install-manifest.txt"
PROXNIX_INSTALL_INFO="${PROXNIX_LIB_DIR}/install-info.txt"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

die()    { echo "ERROR: $*" >&2; exit 1; }
log()    { echo "  $*"; }
action() { echo ""; echo "→ $*"; }

do_rm() {
    local path="$1"
    if [[ ! -e "$path" && ! -L "$path" ]]; then
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

remove_deploy_gcroots() {
    local root
    if [[ ! -d "$PROXNIX_DEPLOY_GCROOT_DIR" ]]; then
        log "(already absent) $PROXNIX_DEPLOY_GCROOT_DIR"
        return
    fi
    for root in "$PROXNIX_DEPLOY_GCROOT_DIR"/*; do
        [[ -e "$root" || -L "$root" ]] || continue
        do_rm "$root"
    done
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
do_rm "$LXC_HOOKS_DIR/nixos-proxnix-poststop"

action "Local runtime helper"
do_rm "$PROXNIX_LIB_DIR/pve-conf-to-nix.py"
do_rm "$PROXNIX_LIB_DIR/proxnix_authority_render.py"
do_rm "$PROXNIX_LIB_DIR/proxnix_reconciler_state.py"
do_rm "$PROXNIX_LIB_DIR/nixos-proxnix-common.sh"
do_rm "$PROXNIX_LIB_DIR/proxnix-secrets-guest"
do_rm "$PROXNIX_INSTALL_MANIFEST"
do_rm "$PROXNIX_INSTALL_INFO"

action "Local admin helper"
do_rm "$PROXNIX_SBIN_DIR/proxnix-doctor"
do_rm "$PROXNIX_SBIN_DIR/proxnix-create-lxc"
do_rm "$PROXNIX_SBIN_DIR/proxnix-authority-render"
do_rm "$PROXNIX_SBIN_DIR/proxnix-gc"
do_rm "$PROXNIX_SBIN_DIR/proxnix-reconcile"
do_rm "$PROXNIX_SBIN_DIR/proxnix-reconcile-build-golden"
do_rm "$PROXNIX_SBIN_DIR/proxnix-reconcile-build"
do_rm "$PROXNIX_SBIN_DIR/proxnix-reconcile-seed"
do_rm "$PROXNIX_SBIN_DIR/proxnix-reconcile-seed-offline"
do_rm "$PROXNIX_SBIN_DIR/proxnix-reconcile-activate"
do_rm "$PROXNIX_SBIN_DIR/proxnix-reconciler-state"
do_rm "$PROXNIX_SBIN_DIR/proxnix-host-activate"
do_rm "$PROXNIX_SBIN_DIR/proxnix-host-uninstall"
do_rm "$PROXNIX_SBIN_DIR/proxnix-uninstall"

action "Nix-managed host runtime"
do_rm "$PROXNIX_LOCAL_BIN_DIR/age"
do_rm "$PROXNIX_LOCAL_BIN_DIR/jq"
do_rm "$PROXNIX_LOCAL_BIN_DIR/rsync"
do_rm "$PROXNIX_LOCAL_BIN_DIR/sops"
for profile_path in \
    "$PROXNIX_HOST_PROFILE" "$PROXNIX_HOST_PROFILE"-*-link \
    "$PROXNIX_LEGACY_HOST_TOOLS_PROFILE" "$PROXNIX_LEGACY_HOST_TOOLS_PROFILE"-*-link
do
    [[ -e "$profile_path" || -L "$profile_path" ]] || continue
    do_rm "$profile_path"
done

action "GC timer"
if [[ $DRY_RUN -eq 0 ]]; then
    systemctl disable --now proxnix-gc.timer 2>/dev/null || true
fi
do_rm "$SYSTEMD_UNIT_DIR/proxnix-gc.timer"
do_rm "$SYSTEMD_UNIT_DIR/proxnix-gc.service"
if [[ $DRY_RUN -eq 0 ]]; then
    systemctl daemon-reload
fi

action "Reconciler timer"
if [[ $DRY_RUN -eq 0 ]]; then
    systemctl disable --now proxnix-reconcile.timer 2>/dev/null || true
fi
do_rm "$SYSTEMD_UNIT_DIR/proxnix-reconcile.timer"
do_rm "$SYSTEMD_UNIT_DIR/proxnix-reconcile.service"
do_rm "$SYSTEMD_UNIT_DIR/proxnix-reconcile@.service"
if [[ $DRY_RUN -eq 0 ]]; then
    systemctl daemon-reload
fi

action "Deployment GC roots"
remove_deploy_gcroots
do_rmdir_if_empty "$PROXNIX_DEPLOY_GCROOT_DIR"
do_rmdir_if_empty "$PROXNIX_DATA_DIR/gcroots"

do_rmdir_if_empty "$PROXNIX_LIB_DIR"

echo ""
echo "Done."
echo ""
echo "  /var/lib/proxnix/, /var/lib/proxnix/authority/, /var/lib/proxnix/status/,"
echo "  /nix, and /etc/proxnix/ were otherwise not touched."
echo "  Published relay-cache config and secret material are still intact."
echo ""
echo "  To fully remove proxnix data from this node, delete:"
echo "    rm -rf /var/lib/proxnix /etc/proxnix"
