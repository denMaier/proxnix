#!/bin/bash

NIXLXC_DIR="/var/lib/proxnix"
NIXLXC_PRIV_DIR="/var/lib/proxnix/private"
PROXNIX_HOST_STATE_DIR="/etc/proxnix"
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

proxnix_container_dir() {
    local vmid="$1"
    printf '%s/containers/%s' "$NIXLXC_DIR" "$vmid"
}

proxnix_container_priv_dir() {
    local vmid="$1"
    printf '%s/containers/%s' "$NIXLXC_PRIV_DIR" "$vmid"
}

proxnix_host_relay_identity_path() {
    printf '%s/host_relay_identity' "$PROXNIX_HOST_STATE_DIR"
}

proxnix_container_age_identity_store_path() {
    local vmid="$1"
    printf '%s/age_identity.sops.json' "$(proxnix_container_priv_dir "$vmid")"
}

proxnix_shared_age_identity_store_path() {
    printf '%s/shared_age_identity.sops.json' "$NIXLXC_PRIV_DIR"
}

proxnix_with_host_relay_key() {
    local relay_key
    relay_key="$(proxnix_host_relay_identity_path)"
    [[ -f "$relay_key" ]] || return 1
    env -u SOPS_AGE_KEY_FILE SOPS_AGE_SSH_PRIVATE_KEY_FILE="$relay_key" "$@"
}

proxnix_decrypt_host_identity_store_to_file() {
    local store="$1" out="$2" tmp_json
    [[ -f "$store" ]] || return 1
    tmp_json="$(mktemp /tmp/proxnix-host-identity-json.XXXXXX)"
    if ! proxnix_with_host_relay_key sops decrypt --input-type json --output-type json "$store" > "$tmp_json"; then
        rm -f "$tmp_json" "$out"
        return 1
    fi
    if ! python3 - "$tmp_json" "$out" <<'PY'
import json
import sys

with open(sys.argv[1]) as source:
    payload = json.load(source)

with open(sys.argv[2], "w") as fh:
    fh.write(payload["identity"])
PY
    then
        rm -f "$tmp_json" "$out"
        return 1
    fi
    rm -f "$tmp_json"
    chmod 600 "$out"
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
