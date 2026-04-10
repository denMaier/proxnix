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
#                 (use after upgrading proxnix to push new base.nix etc.)
#
# Per-node (every cluster node, not replicated):
#   /usr/share/lxc/config/nixos.common.conf     — auto-included for ostype=nixos
#   /usr/share/lxc/config/nixos.userns.conf     — auto-included for unprivileged
#   /usr/share/lxc/hooks/nixos-proxnix-prestart — pre-start hook (auto-runs)
#   /usr/local/lib/proxnix/yaml-to-nix.py       — local runtime helper
#
# Shared (first node only, replicated via pmxcfs to all nodes):
#   /etc/pve/proxnix/base.nix                   — shared NixOS base config
#   /etc/pve/proxnix/configuration.nix          — shared NixOS entrypoint
#   /etc/pve/proxnix/chezmoi.nix                — chezmoi module
#   /etc/pve/proxnix/containers/                — per-container config + pubkeys
#   /etc/pve/priv/proxnix/containers/           — per-container encrypted secrets
#
# proxnix-secrets (local workstation tool) has its own install instructions:
#   cp proxnix-secrets ~/.local/bin/
#   chmod +x ~/.local/bin/proxnix-secrets

set -euo pipefail

LXC_CONFIG_DIR="/usr/share/lxc/config"
LXC_HOOKS_DIR="/usr/share/lxc/hooks"
PROXNIX_LIB_DIR="/usr/local/lib/proxnix"
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

# ── Pre-start hook ────────────────────────────────────────────────────────────
# Runs during lxc-start, after the rootfs is mounted.
# Registered via lxc.hook.pre-start in nixos.common.conf — no per-container
# setup needed.

action "Pre-start hook → $LXC_HOOKS_DIR/"
do_install "$SCRIPT_DIR/lxc/hooks/nixos-proxnix-prestart" \
           "$LXC_HOOKS_DIR/nixos-proxnix-prestart" "755"

# ── Local runtime helper ──────────────────────────────────────────────────────
# Must exist on every node because the hook runs locally during container
# startup. Keep it out of pmxcfs so we never depend on executable metadata in
# /etc/pve.

action "Local runtime helper → $PROXNIX_LIB_DIR/"
do_install "$SCRIPT_DIR/yaml-to-nix.py" "$PROXNIX_LIB_DIR/yaml-to-nix.py" "755"

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
    do_install "$SCRIPT_DIR/configuration.nix" "$NIXLXC_DIR/configuration.nix"
    do_install "$SCRIPT_DIR/chezmoi.nix"       "$NIXLXC_DIR/chezmoi.nix"
    do_mkdir "$NIXLXC_DIR/containers"
    do_mkdir "$NIXLXC_PRIV_DIR/containers"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "Done."
echo ""
echo "Next steps:"
echo ""
echo "  1. Set a master age key for secret backup/recovery:"
echo "       # SSH public key (easiest):"
echo "       ssh-keygen -y -f ~/.ssh/id_ed25519 > $NIXLXC_DIR/master_age_pubkey"
echo "       # or generate a dedicated age key:"
echo "       age-keygen 2>&1 | grep 'public key' | awk '{print \$NF}' \\"
echo "         > $NIXLXC_DIR/master_age_pubkey"
echo ""
echo "  2. Create a NixOS CT in the Proxmox WebUI (or pct create):"
echo "       # Ensure ostype=nixos so the proxnix hook is auto-included."
echo "       # If you plan to use Podman, also enable features: nesting=1."
echo "       # Hostname/IP/gateway/DNS/SSH keys from the WebUI are mirrored"
echo "       # into generated Nix on first boot."
echo ""
echo "  3. Optional: add per-container config under $NIXLXC_DIR/containers/<vmid>/"
echo "       mkdir -p $NIXLXC_DIR/containers/<vmid>"
echo "       # proxmox.yaml is optional — use it for search_domain or ssh_keys."
echo "       # Add user.yaml to declare containers/services."
echo ""
echo "  4. Start the container — the hook seeds /etc/nixos before boot:"
echo "       pct start <vmid>"
echo ""
echo "       # First activation now happens automatically during boot."
echo "       # Watch it with:"
echo "       pct exec <vmid> -- journalctl -u proxnix-first-boot-rebuild -b"
echo ""
echo "  5. After first boot, bootstrap age encryption for secrets:"
echo "       $SCRIPT_DIR/bootstrap.sh <vmid>"
