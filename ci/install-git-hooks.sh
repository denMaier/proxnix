#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_DIR="${ROOT_DIR}/.githooks"

[[ -d "$HOOKS_DIR" ]] || {
  echo "error: hook directory not found: ${HOOKS_DIR}" >&2
  exit 1
}

git config core.hooksPath ".githooks"
echo "Configured git hooks path: .githooks"
echo "Active hooks:"
find "$HOOKS_DIR" -maxdepth 1 -type f -perm -111 -exec basename {} \; | sort
