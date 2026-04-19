#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VERSION_FILE="${ROOT_DIR}/VERSION"
PYPROJECT="${ROOT_DIR}/workstation/pyproject.toml"

usage() {
  cat <<'EOF'
Usage:
  ./ci/set-version.sh <version>

Example:
  ./ci/set-version.sh 1.2.3
  ./ci/set-version.sh 1.2.3-rc1
EOF
}

case "${1:-}" in
  ""|--help|-h)
    usage
    exit 0
    ;;
esac

version="${1:-}"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/release-lib.sh"
version="$(validate_release_version "$version")"

printf '%s\n' "$version" > "$VERSION_FILE"

python3 - <<'PY' "$PYPROJECT" "$version"
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
version = sys.argv[2]
text = path.read_text(encoding="utf-8")
new_text, count = re.subn(
    r'(?m)^(version\s*=\s*")([^"]+)("\s*)$',
    rf'\g<1>{version}\g<3>',
    text,
    count=1,
)
if count != 1:
    raise SystemExit("error: could not update [project].version in workstation/pyproject.toml")
path.write_text(new_text, encoding="utf-8")
PY

printf 'Set project version to %s\n' "$version"
