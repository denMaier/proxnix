#!/usr/bin/env bash

set -euo pipefail

die() {
  echo "error: $*" >&2
  exit 1
}

[[ $# -gt 0 ]] || die "usage: $(basename "$0") <file> [<file> ...]"
[[ -n "${CODEBERG_TOKEN:-}" ]] || die "CODEBERG_TOKEN is required"
[[ -n "${CODEBERG_PACKAGE_NAME:-}" ]] || die "CODEBERG_PACKAGE_NAME is required"
[[ -n "${CODEBERG_PACKAGE_VERSION:-}" ]] || die "CODEBERG_PACKAGE_VERSION is required"

CODEBERG_SERVER="${CODEBERG_SERVER:-https://codeberg.org}"
CODEBERG_PACKAGE_OWNER="${CODEBERG_PACKAGE_OWNER:-${GITHUB_REPOSITORY_OWNER:-}}"

if [[ -z "$CODEBERG_PACKAGE_OWNER" && -n "${GITHUB_REPOSITORY:-}" ]]; then
  CODEBERG_PACKAGE_OWNER="${GITHUB_REPOSITORY%%/*}"
fi

[[ -n "$CODEBERG_PACKAGE_OWNER" ]] || die "CODEBERG_PACKAGE_OWNER is required"

api_base="${CODEBERG_SERVER%/}/api/packages/${CODEBERG_PACKAGE_OWNER}/generic/${CODEBERG_PACKAGE_NAME}/${CODEBERG_PACKAGE_VERSION}"
auth_user="${CODEBERG_USERNAME:-$CODEBERG_PACKAGE_OWNER}"

if [[ "${CODEBERG_PACKAGE_REPLACE:-0}" == "1" ]]; then
  curl --fail --silent --show-error \
    --user "${auth_user}:${CODEBERG_TOKEN}" \
    --request DELETE \
    "${api_base}" >/dev/null || true
fi

for file in "$@"; do
  [[ -f "$file" ]] || die "file not found: $file"
  filename="$(basename "$file")"
  echo "Uploading ${filename} to ${CODEBERG_PACKAGE_NAME}/${CODEBERG_PACKAGE_VERSION}"
  curl --fail --silent --show-error \
    --user "${auth_user}:${CODEBERG_TOKEN}" \
    --upload-file "$file" \
    "${api_base}/${filename}" >/dev/null
done
