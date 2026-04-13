#!/bin/bash
# install.sh — Install proxnix on a Proxmox host.
#
# Must be run as root on the Proxmox host from this repository directory.
# proxnix now uses node-local relay caches. Run this on every node that should
# host proxnix-managed containers. Publish workstation-owned site data into
# /var/lib/proxnix on every node that should relay it.
#
# Usage:
#   ./install.sh [--dry-run] [--force-shared]
#
# --force-shared  deprecated compatibility flag; node-local config is written
#                 on every install run
#
# Per-node runtime assets:
#   /usr/share/lxc/config/nixos.common.conf     — auto-included for ostype=nixos
#   /usr/share/lxc/config/nixos.userns.conf     — auto-included for unprivileged
#   /usr/share/lxc/hooks/nixos-proxnix-prestart — pre-start render hook
#   /usr/share/lxc/hooks/nixos-proxnix-mount    — mount-time sync hook
#   /usr/local/lib/proxnix/yaml-to-nix.py       — local runtime helper
#   /usr/local/lib/proxnix/nixos-proxnix-common.sh
#                                               — shared hook helper
#   /usr/local/lib/proxnix/proxnix-secrets-guest
#                                               — helper injected into guests
#   /usr/local/sbin/proxnix-create-lxc          — CT creation helper
#
# Node-local proxnix data:
#   /var/lib/proxnix/base.nix                   — shared install baseline
#   /var/lib/proxnix/common.nix                 — shared proxnix option module
#   /var/lib/proxnix/configuration.nix          — shared NixOS entrypoint
#   /var/lib/proxnix/site.nix                   — optional site/data-repo override
#   /var/lib/proxnix/containers/                — per-container config relay cache
#   /var/lib/proxnix/private/shared/            — shared encrypted secrets
#   /var/lib/proxnix/private/containers/        — per-container encrypted secrets
#
set -euo pipefail

LXC_CONFIG_DIR="/usr/share/lxc/config"
LXC_HOOKS_DIR="/usr/share/lxc/hooks"
PROXNIX_LIB_DIR="/usr/local/lib/proxnix"
PROXNIX_SBIN_DIR="/usr/local/sbin"
SYSTEMD_UNIT_DIR="/etc/systemd/system"
NIXLXC_DIR="/var/lib/proxnix"
NIXLXC_PRIV_DIR="/var/lib/proxnix/private"

DRY_RUN=0
FORCE_SHARED=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY_RUN=1 ;;
        --force-shared) FORCE_SHARED=1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── helpers ───────────────────────────────────────────────────────────────────

die()    { echo "ERROR: $*" >&2; exit 1; }
log()    { echo "  $*"; }
action() { echo ""; echo "→ $*"; }

do_install() {
    local src="$1" dest="$2" mode="${3:-644}"
    [[ -f "$src" ]] || die "source not found: $src"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "[dry-run] $src → $dest (mode $mode)"
        return
    fi
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
    chmod "$mode" "$dest"
    log "$dest"
}

do_systemd_timer() {
    local name="$1"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "[dry-run] install + enable $name.{service,timer}"
        return
    fi
    do_install "$SCRIPT_DIR/systemd/${name}.service" "$SYSTEMD_UNIT_DIR/${name}.service" "644"
    do_install "$SCRIPT_DIR/systemd/${name}.timer"   "$SYSTEMD_UNIT_DIR/${name}.timer"   "644"
    systemctl daemon-reload
    systemctl enable --now "${name}.timer"
    log "${name}.timer enabled"
}

do_mkdir() {
    local dir="$1" mode="${2:-}"
    if [[ $DRY_RUN -eq 1 ]]; then
        if [[ -n "$mode" ]]; then
            log "[dry-run] mkdir -p $dir && chmod $mode $dir"
        else
            log "[dry-run] mkdir -p $dir"
        fi
        return
    fi
    mkdir -p "$dir"
    if [[ -n "$mode" ]]; then
        chmod "$mode" "$dir"
    fi
    log "$dir"
}

# ── guards ────────────────────────────────────────────────────────────────────

[[ "$(id -u)" -eq 0 ]] || die "Must be run as root."
command -v pveversion >/dev/null 2>&1 || die "pveversion not found — is this a Proxmox host?"

echo ""
echo "proxnix install"
echo "==============="
[[ $DRY_RUN -eq 1 ]] && echo "(dry run — no files will be written)"

# ── LXC config files ──────────────────────────────────────────────────────────
# Auto-included by lxc-start for every ostype=nixos container.

action "LXC config files → $LXC_CONFIG_DIR/"
do_install "$SCRIPT_DIR/lxc/config/nixos.common.conf"  "$LXC_CONFIG_DIR/nixos.common.conf"
do_install "$SCRIPT_DIR/lxc/config/nixos.userns.conf"  "$LXC_CONFIG_DIR/nixos.userns.conf"

# ── Host lifecycle hooks ──────────────────────────────────────────────────────
# The pre-start hook renders desired state on the host into /run/proxnix/<vmid>.
# The mount hook then copies that staged state into the mounted rootfs.

action "Lifecycle hooks → $LXC_HOOKS_DIR/"
do_install "$SCRIPT_DIR/lxc/hooks/nixos-proxnix-prestart" \
           "$LXC_HOOKS_DIR/nixos-proxnix-prestart" "755"
do_install "$SCRIPT_DIR/lxc/hooks/nixos-proxnix-mount" \
           "$LXC_HOOKS_DIR/nixos-proxnix-mount" "755"

# ── Local runtime helper ──────────────────────────────────────────────────────
# Must exist on every node because the hooks run locally during container
# startup. Keep them outside the mutable node-local data tree so executable
# metadata is managed like normal local files.

action "Local runtime helper → $PROXNIX_LIB_DIR/"
do_install "$SCRIPT_DIR/yaml-to-nix.py" "$PROXNIX_LIB_DIR/yaml-to-nix.py" "755"
do_install "$SCRIPT_DIR/lxc/hooks/nixos-proxnix-common.sh" \
           "$PROXNIX_LIB_DIR/nixos-proxnix-common.sh" "644"
do_install "$SCRIPT_DIR/proxnix-secrets-guest" \
           "$PROXNIX_LIB_DIR/proxnix-secrets-guest" "755"

# ── Local admin helper ────────────────────────────────────────────────────────

action "Local admin helper → $PROXNIX_SBIN_DIR/"
do_install "$SCRIPT_DIR/proxnix-doctor" "$PROXNIX_SBIN_DIR/proxnix-doctor" "755"
do_install "$SCRIPT_DIR/proxnix-create-lxc" "$PROXNIX_SBIN_DIR/proxnix-create-lxc" "755"

# ── GC timer ──────────────────────────────────────────────────────────────────
# Cleans up stale /run/proxnix/<vmid>/ dirs every 15 min for stopped/deleted
# containers.  Stage dirs live on tmpfs and the mount hook can't remove them
# (runs as subuid, not root).

action "GC timer → $SYSTEMD_UNIT_DIR/"
do_systemd_timer "proxnix-gc"

# ── Node-local proxnix data → /var/lib/proxnix/ ──────────────────────────────
# Keep only data here, not runnable scripts. This repo owns the install layer;
# a separate site/data repo can manage site.nix, containers/, and the
# encrypted secrets trees. Users are responsible for syncing those files across
# nodes if they want shared behavior.

action "Node-local proxnix data → $NIXLXC_DIR/ + $NIXLXC_PRIV_DIR/"
if [[ $FORCE_SHARED -eq 1 ]]; then
    echo "  note: --force-shared is deprecated in node-local mode and is ignored"
fi
do_mkdir "$NIXLXC_DIR" "0755"
do_mkdir "$NIXLXC_PRIV_DIR" "0700"
do_install "$SCRIPT_DIR/base.nix"          "$NIXLXC_DIR/base.nix"
do_install "$SCRIPT_DIR/common.nix"        "$NIXLXC_DIR/common.nix"
do_install "$SCRIPT_DIR/configuration.nix" "$NIXLXC_DIR/configuration.nix"
do_mkdir "$NIXLXC_DIR/containers" "0755"
do_mkdir "$NIXLXC_PRIV_DIR/shared" "0700"
do_mkdir "$NIXLXC_PRIV_DIR/containers" "0700"

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "Done."
echo ""
echo "Next steps:"
echo ""
echo "  1. On your workstation, manage proxnix state from a separate site repo:"
echo "       # site.nix, containers/<vmid>/..., encrypted secret stores,"
echo "       # and encrypted private identities all live there"
echo "       proxnix-secrets init-shared"
echo "       proxnix-publish"
echo ""
echo "  2. Create a NixOS CT in the Proxmox WebUI, with pct create, or with:"
echo "       proxnix-create-lxc"
echo "       # Ensure ostype=nixos so the proxnix hook is auto-included."
echo "       # If you plan to use Podman, also enable features: nesting=1."
echo "       # Hostname/IP/gateway/DNS/SSH keys from the WebUI are mirrored"
echo "       # into generated Nix on first boot."
echo ""
echo "  3. Publish workstation-managed relay state to this node before booting:"
echo "       proxnix-publish"
echo "       # This syncs site.nix, containers/<vmid>/..., encrypted secret"
echo "       # stores, and staged private identities into $NIXLXC_DIR."
echo ""
echo "  4. Start the container — pre-start renders state, mount seeds /etc/nixos before boot:"
echo "       pct start <vmid>"
echo ""
echo "       # The mount hook now installs a host-managed service under"
echo "       # /etc/systemd/system.attached so first boot bootstraps channels"
echo "       # and applies managed config automatically when the hash changes."
echo "       # Watch it with:"
echo "       pct exec <vmid> -- journalctl -u proxnix-apply-config.service -b"
echo ""
echo "  5. Run health checks anytime:"
echo "       proxnix-doctor <vmid>"
echo "       # Inside the guest, run: proxnix-help"
