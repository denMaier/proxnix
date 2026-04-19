#!/bin/bash
# install.sh — Install proxnix on a Proxmox host.
#
# Must be run as root on the Proxmox host from this repository directory.
# proxnix now uses node-local relay caches. Run this on every node that should
# host proxnix-managed containers. Publish workstation-owned site data into
# /var/lib/proxnix on every node that should relay it.
#
# Usage:
#   host/install.sh [--dry-run] [--force-shared]
#
# --force-shared  deprecated compatibility flag; node-local config is written
#                 on every install run
#
# Per-node runtime assets:
#   /usr/share/lxc/config/nixos.common.conf     — auto-included for ostype=nixos
#   /usr/share/lxc/config/nixos.userns.conf     — auto-included for unprivileged
#   /usr/share/lxc/hooks/nixos-proxnix-prestart — pre-start render hook
#   /usr/share/lxc/hooks/nixos-proxnix-mount    — mount-time sync hook
#   /usr/local/lib/proxnix/pve-conf-to-nix.py   — local runtime helper
#   /usr/local/lib/proxnix/nixos-proxnix-common.sh
#                                               — shared hook helper
#   /usr/local/lib/proxnix/proxnix-secrets-guest
#                                               — helper injected into guests
#   /usr/local/lib/proxnix/install-manifest.txt — installed-file manifest
#   /usr/local/lib/proxnix/install-info.txt     — local install metadata
#   /usr/local/sbin/proxnix-create-lxc          — CT creation helper
#   /usr/local/sbin/proxnix-uninstall           — local uninstall helper
#
# Node-local proxnix data:
#   /var/lib/proxnix/base.nix                   — shared install baseline
#   /var/lib/proxnix/common.nix                 — shared proxnix option module
#   /var/lib/proxnix/security-policy.nix        — shared host-enforced security policy
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
PROXNIX_HOST_STATE_DIR="/etc/proxnix"
PROXNIX_INSTALL_MANIFEST="${PROXNIX_LIB_DIR}/install-manifest.txt"
PROXNIX_INSTALL_INFO="${PROXNIX_LIB_DIR}/install-info.txt"

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

do_write_text() {
    local dest="$1" mode="$2" content="$3"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "[dry-run] write $dest (mode $mode)"
        return
    fi
    mkdir -p "$(dirname "$dest")"
    printf '%s' "$content" > "$dest"
    chmod "$mode" "$dest"
    log "$dest"
}

# ── guards ────────────────────────────────────────────────────────────────────

[[ "$(id -u)" -eq 0 ]] || die "Must be run as root."
command -v pveversion >/dev/null 2>&1 || die "pveversion not found — is this a Proxmox host?"
command -v sops >/dev/null 2>&1 || die "sops not found — install it on the Proxmox host; proxnix uses it at boot to decrypt relay-encrypted guest identities"

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
# pre-start renders desired state on the host into /run/proxnix/<vmid>/, scoped
# to the container's host-side root UID where bind mounts need it. The mount
# hook bind-mounts config/runtime state where appropriate, copies secrets into
# the guest as root-owned regular files, and the post-stop hook removes the
# staging dir after the container stops (or if start-up aborts).

action "Lifecycle hooks → $LXC_HOOKS_DIR/"
do_install "$SCRIPT_DIR/lxc/hooks/nixos-proxnix-prestart" \
           "$LXC_HOOKS_DIR/nixos-proxnix-prestart" "755"
do_install "$SCRIPT_DIR/lxc/hooks/nixos-proxnix-mount" \
           "$LXC_HOOKS_DIR/nixos-proxnix-mount" "755"
do_install "$SCRIPT_DIR/lxc/hooks/nixos-proxnix-poststop" \
           "$LXC_HOOKS_DIR/nixos-proxnix-poststop" "755"

# ── Local runtime helper ──────────────────────────────────────────────────────
# Must exist on every node because the hooks run locally during container
# startup. Keep them outside the mutable node-local data tree so executable
# metadata is managed like normal local files.

action "Local runtime helper → $PROXNIX_LIB_DIR/"
do_install "$SCRIPT_DIR/pve-conf-to-nix.py" "$PROXNIX_LIB_DIR/pve-conf-to-nix.py" "755"
do_install "$SCRIPT_DIR/lxc/hooks/nixos-proxnix-common.sh" \
           "$PROXNIX_LIB_DIR/nixos-proxnix-common.sh" "644"
do_install "$SCRIPT_DIR/proxnix-secrets-guest" \
           "$PROXNIX_LIB_DIR/proxnix-secrets-guest" "755"

# ── Local admin helper ────────────────────────────────────────────────────────

action "Local admin helper → $PROXNIX_SBIN_DIR/"
do_install "$SCRIPT_DIR/proxnix-doctor" "$PROXNIX_SBIN_DIR/proxnix-doctor" "755"
do_install "$SCRIPT_DIR/proxnix-create-lxc" "$PROXNIX_SBIN_DIR/proxnix-create-lxc" "755"
do_install "$SCRIPT_DIR/uninstall.sh" "$PROXNIX_SBIN_DIR/proxnix-uninstall" "755"

# ── GC timer ──────────────────────────────────────────────────────────────────
# Belt-and-suspenders cleanup for orphaned /run/proxnix/<vmid>/ dirs (e.g. host
# crash, Proxmox restart). The mount hook and poststop hook handle the normal
# cases; this catches anything left behind by abnormal termination.

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
do_mkdir "$PROXNIX_HOST_STATE_DIR" "0700"
do_mkdir "$PROXNIX_STAGE_BASE_DIR" "0711"
do_install "$SCRIPT_DIR/base.nix"          "$NIXLXC_DIR/base.nix"
do_install "$SCRIPT_DIR/common.nix"        "$NIXLXC_DIR/common.nix"
do_install "$SCRIPT_DIR/security-policy.nix" "$NIXLXC_DIR/security-policy.nix"
do_install "$SCRIPT_DIR/configuration.nix" "$NIXLXC_DIR/configuration.nix"
do_mkdir "$NIXLXC_DIR/containers" "0755"
do_mkdir "$NIXLXC_PRIV_DIR/shared" "0700"
do_mkdir "$NIXLXC_PRIV_DIR/containers" "0700"

action "Install metadata → $PROXNIX_LIB_DIR/"
do_write_text "$PROXNIX_INSTALL_MANIFEST" "644" "$(cat <<EOF
/usr/share/lxc/config/nixos.common.conf
/usr/share/lxc/config/nixos.userns.conf
/usr/share/lxc/hooks/nixos-proxnix-prestart
/usr/share/lxc/hooks/nixos-proxnix-mount
/usr/share/lxc/hooks/nixos-proxnix-poststop
/usr/local/lib/proxnix/pve-conf-to-nix.py
/usr/local/lib/proxnix/nixos-proxnix-common.sh
/usr/local/lib/proxnix/proxnix-secrets-guest
/usr/local/lib/proxnix/install-manifest.txt
/usr/local/lib/proxnix/install-info.txt
/usr/local/sbin/proxnix-doctor
/usr/local/sbin/proxnix-create-lxc
/usr/local/sbin/proxnix-uninstall
/etc/systemd/system/proxnix-gc.service
/etc/systemd/system/proxnix-gc.timer
/var/lib/proxnix/base.nix
/var/lib/proxnix/common.nix
/var/lib/proxnix/security-policy.nix
/var/lib/proxnix/configuration.nix
EOF
)"
do_write_text "$PROXNIX_INSTALL_INFO" "644" "$(cat <<EOF
proxnix host install
====================

This node no longer depends on the original proxnix repo checkout for normal
operations.

Installed local commands:
- proxnix-create-lxc
- proxnix-doctor
- proxnix-uninstall

Managed local runtime files are listed in:
${PROXNIX_INSTALL_MANIFEST}

Published relay data remains external:
- ${NIXLXC_DIR}
- ${PROXNIX_HOST_STATE_DIR}

To remove only the installed proxnix runtime from this node, run:
  proxnix-uninstall
EOF
)"

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "Done."
echo ""
echo "This node does not need the original proxnix repo checkout after install."
echo "Use 'proxnix-uninstall' on the host if you later want to remove the installed runtime."
echo ""
echo "Next steps:"
echo ""
echo "  1. On your workstation, manage proxnix state from a separate site repo:"
echo "       # site.nix, containers/<vmid>/..., encrypted secret stores,"
echo "       # and encrypted private identities all live there"
echo "       proxnix-secrets init-host-relay"
echo "       proxnix-publish"
echo ""
echo "  2. Create a NixOS CT in the Proxmox WebUI, with pct create, or with:"
echo "       proxnix-create-lxc"
echo "       # Ensure ostype=nixos so the proxnix hook is auto-included."
echo "       # NixOS CTs created via proxnix-create-lxc use features: nesting=1,keyctl=1."
echo "       # Hostname/IP/gateway/DNS/SSH keys from the WebUI are mirrored"
echo "       # into generated Nix on first boot."
echo ""
echo "  3. Publish workstation-managed relay state to this node before booting:"
echo "       proxnix-publish"
echo "       # This syncs site.nix, containers/<vmid>/..., encrypted secret"
echo "       # stores and relay-encrypted guest identities into $NIXLXC_DIR,"
echo "       # plus the shared host relay key into $PROXNIX_HOST_STATE_DIR."
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
