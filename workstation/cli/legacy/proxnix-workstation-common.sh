#!/bin/bash

CONFIG_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/proxnix/config"

expand_home_path() {
  local value="${1:-}"
  if [[ "$value" == "~" ]]; then
    printf '%s\n' "$HOME"
  elif [[ "$value" == ~/* ]]; then
    printf '%s/%s\n' "$HOME" "${value#~/}"
  else
    printf '%s\n' "$value"
  fi
}

load_proxnix_workstation_config() {
  PROXNIX_SITE_DIR="${PROXNIX_SITE_DIR:-}"
  PROXNIX_MASTER_IDENTITY="${PROXNIX_MASTER_IDENTITY:-$HOME/.ssh/id_ed25519}"
  PROXNIX_HOSTS="${PROXNIX_HOSTS:-}"
  PROXNIX_SSH_IDENTITY="${PROXNIX_SSH_IDENTITY:-}"
  PROXNIX_REMOTE_DIR="${PROXNIX_REMOTE_DIR:-/var/lib/proxnix}"
  PROXNIX_REMOTE_PRIV_DIR="${PROXNIX_REMOTE_PRIV_DIR:-/var/lib/proxnix/private}"
  PROXNIX_REMOTE_HOST_RELAY_IDENTITY="${PROXNIX_REMOTE_HOST_RELAY_IDENTITY:-/etc/proxnix/host_relay_identity}"

  [[ -f "$CONFIG_FILE" ]] && source "$CONFIG_FILE"

  PROXNIX_SITE_DIR="$(expand_home_path "$PROXNIX_SITE_DIR")"
  PROXNIX_MASTER_IDENTITY="$(expand_home_path "$PROXNIX_MASTER_IDENTITY")"
  PROXNIX_SSH_IDENTITY="$(expand_home_path "$PROXNIX_SSH_IDENTITY")"
  PROXNIX_REMOTE_DIR="$(expand_home_path "$PROXNIX_REMOTE_DIR")"
  PROXNIX_REMOTE_PRIV_DIR="$(expand_home_path "$PROXNIX_REMOTE_PRIV_DIR")"
  PROXNIX_REMOTE_HOST_RELAY_IDENTITY="$(expand_home_path "$PROXNIX_REMOTE_HOST_RELAY_IDENTITY")"
}

proxnix_site_private_dir() {
  printf '%s/private' "$PROXNIX_SITE_DIR"
}

proxnix_site_shared_store() {
  printf '%s/shared/secrets.sops.yaml' "$(proxnix_site_private_dir)"
}

proxnix_site_container_store() {
  local vmid="$1"
  printf '%s/containers/%s/secrets.sops.yaml' "$(proxnix_site_private_dir)" "$vmid"
}

proxnix_site_group_store() {
  local group="$1"
  printf '%s/groups/%s/secrets.sops.yaml' "$(proxnix_site_private_dir)" "$group"
}

proxnix_site_container_secret_groups_file() {
  local vmid="$1"
  printf '%s/containers/%s/secret-groups.list' "$PROXNIX_SITE_DIR" "$vmid"
}

proxnix_site_host_relay_identity_store() {
  printf '%s/host_relay_identity.sops.yaml' "$(proxnix_site_private_dir)"
}

proxnix_site_relay_cache_dir() {
  printf '%s/.relay-cache' "$(proxnix_site_private_dir)"
}

proxnix_site_relay_cache_shared_identity() {
  printf '%s/shared_age_identity.sops.yaml' "$(proxnix_site_relay_cache_dir)"
}

proxnix_site_relay_cache_container_identity() {
  local vmid="$1"
  printf '%s/containers/%s/age_identity.sops.yaml' "$(proxnix_site_relay_cache_dir)" "$vmid"
}

proxnix_site_shared_identity_store() {
  printf '%s/shared_age_identity.sops.yaml' "$(proxnix_site_private_dir)"
}

proxnix_site_container_identity_store() {
  local vmid="$1"
  printf '%s/containers/%s/age_identity.sops.yaml' "$(proxnix_site_private_dir)" "$vmid"
}

proxnix_valid_secret_group_name() {
  local group="${1:-}"
  [[ -n "$group" && "$group" =~ ^[A-Za-z0-9_.-]+$ ]]
}

proxnix_read_container_secret_groups() {
  local vmid="$1" path line
  path="$(proxnix_site_container_secret_groups_file "$vmid")"
  [[ -f "$path" ]] || return 0

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" ]] || continue
    proxnix_valid_secret_group_name "$line" \
      || die "invalid secret group name in ${path}: ${line}"
    printf '%s\n' "$line"
  done < "$path" | awk '!seen[$0]++'
}

proxnix_sops_path() {
  local name="$1"
  if [[ "$name" == \[* ]]; then
    printf '%s' "$name"
  else
    python3 - "$name" <<'PY'
import json
import sys

print("[" + json.dumps(sys.argv[1]) + "]")
PY
  fi
}

proxnix_need_site_tools() {
  [[ -n "$PROXNIX_SITE_DIR" ]] || die "PROXNIX_SITE_DIR not set — create $CONFIG_FILE"
  [[ -d "$PROXNIX_SITE_DIR" ]] || die "site repo directory not found: $PROXNIX_SITE_DIR"
  [[ -f "$PROXNIX_MASTER_IDENTITY" ]] || die "master SSH identity not found: $PROXNIX_MASTER_IDENTITY"
  command -v sops >/dev/null 2>&1 || die "sops not found"
  command -v ssh-keygen >/dev/null 2>&1 || die "ssh-keygen not found"
  command -v python3 >/dev/null 2>&1 || die "python3 not found"
}

proxnix_need_publish_tools() {
  proxnix_need_site_tools
  [[ -n "$PROXNIX_HOSTS" ]] || die "PROXNIX_HOSTS not set — create $CONFIG_FILE"
  if [[ -n "$PROXNIX_SSH_IDENTITY" && ! -f "$PROXNIX_SSH_IDENTITY" ]]; then
    die "publish SSH identity not found: $PROXNIX_SSH_IDENTITY"
  fi
  command -v ssh >/dev/null 2>&1 || die "ssh not found"
  command -v rsync >/dev/null 2>&1 || die "rsync not found"
  command -v mktemp >/dev/null 2>&1 || die "mktemp not found"
}

proxnix_with_master_key() {
  local first_line
  first_line="$(sed -n '1p' "$PROXNIX_MASTER_IDENTITY" 2>/dev/null || true)"
  case "$first_line" in
    -----BEGIN\ OPENSSH\ PRIVATE\ KEY-----|-----BEGIN\ RSA\ PRIVATE\ KEY-----|-----BEGIN\ EC\ PRIVATE\ KEY-----|-----BEGIN\ DSA\ PRIVATE\ KEY-----)
      env -u SOPS_AGE_KEY_FILE SOPS_AGE_SSH_PRIVATE_KEY_FILE="$PROXNIX_MASTER_IDENTITY" "$@"
      ;;
    *)
      die "PROXNIX_MASTER_IDENTITY must point to an SSH private key usable as an age identity: $PROXNIX_MASTER_IDENTITY"
      ;;
  esac
}

proxnix_master_recipient() {
  ssh-keygen -y -f "$PROXNIX_MASTER_IDENTITY" | tr -d '\r\n'
}

proxnix_write_identity_file_payload() {
  local identity_file="$1" out="$2"
  python3 - "$identity_file" "$out" <<'PY'
import sys

with open(sys.argv[1]) as source:
    identity = source.read()

with open(sys.argv[2], "w") as fh:
    fh.write("identity: |\n")
    for line in identity.splitlines():
        fh.write(f"  {line}\n")
PY
}

proxnix_decrypt_identity_to_file() (
  local store="$1" out="$2" tmp_yaml
  tmp_yaml="$(mktemp /tmp/proxnix-identity.XXXXXX.yaml)"
  trap 'rm -f "$tmp_yaml" "$out"' EXIT
  proxnix_with_master_key sops decrypt --input-type yaml --output-type yaml "$store" > "$tmp_yaml"
  python3 - "$tmp_yaml" "$out" <<'PY'
import sys

with open(sys.argv[1]) as source:
    lines = source.readlines()

if not lines or not lines[0].strip().startswith("identity: |"):
    raise SystemExit("invalid proxnix identity payload")

base_indent = None
with open(sys.argv[2], "w") as fh:
    for line in lines[1:]:
        if line.strip() == "":
            fh.write("\n")
            continue
        indent = len(line) - len(line.lstrip(" "))
        if base_indent is None:
            if indent == 0:
                raise SystemExit("invalid proxnix identity payload")
            base_indent = indent
        if indent < base_indent:
            raise SystemExit("invalid proxnix identity payload")
        fh.write(line[base_indent:])
PY
  chmod 600 "$out"
  rm -f "$tmp_yaml"
  trap - EXIT
)

proxnix_identity_public_key_from_store() (
  local store="$1" tmp pubkey
  tmp="$(mktemp /tmp/proxnix-identity.XXXXXX)"
  trap 'rm -f "$tmp"' EXIT
  proxnix_decrypt_identity_to_file "$store" "$tmp"
  pubkey="$(ssh-keygen -y -f "$tmp" | tr -d '\r\n')"
  rm -f "$tmp"
  trap - EXIT
  printf '%s' "$pubkey"
)

proxnix_reencrypt_identity_store_to_file() (
  local source_store="$1" recipients="$2" out="$3" tmp_yaml
  tmp_yaml="$(mktemp /tmp/proxnix-identity.XXXXXX.yaml)"
  trap 'rm -f "$tmp_yaml" "$out"' EXIT
  proxnix_with_master_key sops decrypt --input-type yaml --output-type yaml "$source_store" > "$tmp_yaml"
  proxnix_with_master_key sops --encrypt --age "$recipients" \
    --input-type yaml --output-type yaml "$tmp_yaml" > "$out"
  chmod 600 "$out"
  rm -f "$tmp_yaml"
  trap - EXIT
)

proxnix_top_level_keys() {
  awk '
    /^[A-Za-z0-9_.-]+:/ {
      key=$1
      sub(/:$/, "", key)
      if (key != "sops") print key
    }
  ' | sort -u
}

proxnix_read_secret_value() {
  local value confirm
  if [[ -t 0 ]]; then
    IFS= read -r -s -p "Secret value: " value
    echo
    IFS= read -r -s -p "Confirm: " confirm
    echo
    value="${value%$'\r'}"
    confirm="${confirm%$'\r'}"
    [[ "$value" == "$confirm" ]] || die "values do not match"
  else
    value="$(cat)"
    value="${value%$'\r'}"
  fi
  [[ -n "$value" ]] || die "empty secret value"
  printf '%s' "$value"
}

proxnix_sops_encode_value() {
  local value="$1"
  SECRET_VALUE="$value" python3 - <<'PY'
import json
import os

print(json.dumps(os.environ["SECRET_VALUE"]), end="")
PY
}
