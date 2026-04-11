#!/bin/bash

NIXLXC_DIR="/etc/pve/proxnix"
NIXLXC_PRIV_DIR="/etc/pve/priv/proxnix"
PROXNIX_STAGE_BASE_DIR="/run/proxnix"
PROXNIX_SHARED_FILES=(
    configuration.nix
    base.nix
    common.nix
)

proxnix_stage_dir() {
    local vmid="$1"
    printf '%s/%s' "$PROXNIX_STAGE_BASE_DIR" "$vmid"
}

proxnix_write_if_changed() {
    local src="$1" dest="$2" mode="$3"
    if [[ ! -f "$dest" ]] || ! diff -q "$src" "$dest" >/dev/null 2>&1; then
        cp "$src" "$dest"
        chmod "$mode" "$dest"
        return 0
    fi
    return 1
}

proxnix_write_text_if_changed() {
    local content="$1" dest="$2" mode="$3"
    local tmp
    local changed=0
    tmp="$(mktemp /tmp/proxnix-inline-XXXXXX)"
    printf '%s' "$content" > "$tmp"
    if proxnix_write_if_changed "$tmp" "$dest" "$mode"; then
        changed=1
    fi
    rm -f "$tmp"
    [[ $changed -eq 1 ]]
}

proxnix_hash_tree() {
    local dir="$1"
    (
        cd "$dir" || exit 1
        find . -type f -print0 \
            | LC_ALL=C sort -z \
            | xargs -0 sha256sum \
            | sha256sum \
            | awk '{print $1}'
    )
}

proxnix_set_tree_readonly() {
    local dir="$1"
    [[ -d "$dir" ]] || return 0
    find "$dir" -type d -exec chmod 0555 {} +
    find "$dir" -type f -exec chmod 0444 {} +
}

proxnix_remove_missing_matching_files() {
    local src_dir="$1" dest_dir="$2"
    shift 2
    local find_expr=("$@")
    local changed=1
    local found=0

    [[ -d "$dest_dir" ]] || return 1

    while IFS= read -r -d '' dest; do
        found=1
        local name
        name="$(basename "$dest")"
        if [[ ! -f "${src_dir}/${name}" ]]; then
            rm -f "$dest"
            log "Removed stale file: ${name}"
            changed=0
        fi
    done < <(find "$dest_dir" -maxdepth 1 -type f "${find_expr[@]}" -print0 2>/dev/null)

    if [[ $found -eq 0 ]]; then
        return 1
    fi
    [[ $changed -eq 0 ]]
}

proxnix_sops_store_keys() {
    local store="$1"
    [[ -f "$store" ]] || return 0
    awk '
        /^[A-Za-z0-9_.-]+:/ {
            key=$1
            sub(/:$/, "", key)
            if (key != "sops") print key
        }
    ' "$store" | sort -u
}
