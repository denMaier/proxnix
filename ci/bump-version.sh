#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/release-lib.sh"

usage() {
  cat <<'EOF'
Usage:
  ./ci/bump-version.sh <major|minor|patch>

Examples:
  ./ci/bump-version.sh patch
  ./ci/bump-version.sh minor
  ./ci/bump-version.sh major
EOF
}

kind="${1:-}"
case "$kind" in
  ""|--help|-h)
    usage
    exit 0
    ;;
esac

is_release_bump_kind "$kind" || release_die "invalid bump kind '${kind}' (expected major, minor, or patch)"

current_version="$("${SCRIPT_DIR}/project-version.sh")"
next_version="$(bump_release_version "$current_version" "$kind")"

"${SCRIPT_DIR}/set-version.sh" "$next_version"
