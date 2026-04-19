#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="${ROOT_DIR}/VERSION"

[[ -f "$VERSION_FILE" ]] || {
  echo "error: VERSION file not found: ${VERSION_FILE}" >&2
  exit 1
}

version="$(tr -d '[:space:]' < "$VERSION_FILE")"
[[ -n "$version" ]] || {
  echo "error: VERSION file is empty" >&2
  exit 1
}

printf '%s\n' "$version"
