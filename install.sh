#!/bin/bash
# install.sh — Install proxnix on a Proxmox host.
#
# Must be run as root on the Proxmox host from this repository directory.
# Safe to re-run on additional cluster nodes — the shared /etc/pve/proxnix
# tree and the private /etc/pve/priv/proxnix tree are only written on the
# first node; subsequent nodes skip them.
#
# Usage:
#   ./install.sh [--dry-run] [--force-shared]
#
# --force-shared  overwrite shared pmxcfs content even if it already exists
#                 (use after upgrading proxnix to push new shared .nix files)
#
# Per-node (every cluster node, not replicated):
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
# Shared (first node only, replicated via pmxcfs to all nodes):
#   /etc/pve/proxnix/base.nix                   — shared NixOS base config
#   /etc/pve/proxnix/common.nix                 — shared operator baseline
#   /etc/pve/proxnix/configuration.nix          — shared NixOS entrypoint
#   /etc/pve/proxnix/containers/                — per-container config + pubkeys
#   /etc/pve/priv/proxnix/shared/               — shared encrypted secrets
#   /etc/pve/priv/proxnix/containers/           — per-container encrypted secrets
#
set -euo pipefail

LXC_CONFIG_DIR="/usr/share/lxc/config"
LXC_HOOKS_DIR="/usr/share/lxc/hooks"
PROXNIX_LIB_DIR="/usr/local/lib/proxnix"
PROXNIX_SBIN_DIR="/usr/local/sbin"
SYSTEMD_UNIT_DIR="/etc/systemd/system"
NIXLXC_DIR="/etc/pve/proxnix"
NIXLXC_PRIV_DIR="/etc/pve/priv/proxnix"

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
    if [[ "$dest" == /etc/pve/* ]]; then
        log "$dest (mode managed by pmxcfs path policy)"
        return
    fi
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
    local dir="$1"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "[dry-run] mkdir -p $dir"
        return
    fi
    mkdir -p "$dir"
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
# startup. Keep them out of pmxcfs so we never depend on executable metadata in
# /etc/pve.

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

# ── Shared proxnix files → /etc/pve/{proxnix,priv/proxnix}/ ──────────────────
# Written on the first node only. pmxcfs replicates these directories to every
# other cluster node automatically, so subsequent installs skip this section.
# Keep only shared data here, not runnable scripts, and place encrypted secrets
# under /etc/pve/priv so pmxcfs keeps them root-only.

if [[ -d "$NIXLXC_DIR" && $FORCE_SHARED -eq 0 ]]; then
    action "Shared files → $NIXLXC_DIR/ + $NIXLXC_PRIV_DIR/ (skipped — already exists, replicated via pmxcfs)"
    echo "  (run with --force-shared to overwrite)"
else
    action "Shared files → $NIXLXC_DIR/ + $NIXLXC_PRIV_DIR/  (first node)"
    do_mkdir "$NIXLXC_DIR"
    do_mkdir "$NIXLXC_PRIV_DIR"
    do_install "$SCRIPT_DIR/base.nix"          "$NIXLXC_DIR/base.nix"
    do_install "$SCRIPT_DIR/common.nix"        "$NIXLXC_DIR/common.nix"
    do_install "$SCRIPT_DIR/configuration.nix" "$NIXLXC_DIR/configuration.nix"
    do_mkdir "$NIXLXC_DIR/containers"
    do_mkdir "$NIXLXC_PRIV_DIR/shared"
    do_mkdir "$NIXLXC_PRIV_DIR/containers"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "Done."
echo ""
echo "Next steps:"
echo ""
echo "  1. Set a master SSH-backed age key for secret backup/recovery:"
echo "       ssh-keygen -y -f ~/.ssh/id_ed25519 > $NIXLXC_DIR/master_age_pubkey"
echo ""
echo "  1b. Initialize the shared keypair (if you plan to use shared secrets):"
echo "       proxnix-secrets init-shared"
echo ""
echo "  2. Create a NixOS CT in the Proxmox WebUI, with pct create, or with:"
echo "       proxnix-create-lxc"
echo "       # Ensure ostype=nixos so the proxnix hook is auto-included."
echo "       # If you plan to use Podman, also enable features: nesting=1."
echo "       # Hostname/IP/gateway/DNS/SSH keys from the WebUI are mirrored"
echo "       # into generated Nix on first boot."
echo ""
echo "  3. Optional: add per-container config under $NIXLXC_DIR/containers/<vmid>/"
echo "       mkdir -p $NIXLXC_DIR/containers/<vmid>/quadlets"
echo "       # proxmox.yaml is optional — use it for search_domain or ssh_keys."
echo "       # Add user.yaml only for native NixOS services."
echo "       # Put Quadlet files and their app config under quadlets/."
echo "       # Unit files go to /etc/containers/systemd; app config goes"
echo "       # to /etc/proxnix/quadlets and is tracked with jj."
echo ""
echo "  4. Start the container — pre-start renders state, mount seeds /etc/nixos before boot:"
echo "       pct start <vmid>"
echo ""
echo "       # The mount hook now installs a host-managed service under"
echo "       # /etc/systemd/system.attached so activation runs automatically when"
echo "       # the managed config hash changes."
echo "       # Watch it with:"
echo "       pct exec <vmid> -- journalctl -u proxnix-apply-config.service -b"
echo ""
echo "  5. After first boot, bootstrap guest secrets:"
echo "       $SCRIPT_DIR/bootstrap-guest-secrets.sh <vmid>"
echo ""
echo "  6. Run health checks anytime:"
echo "       proxnix-doctor <vmid>"
echo "       # Inside the guest, run: proxnix-help"
