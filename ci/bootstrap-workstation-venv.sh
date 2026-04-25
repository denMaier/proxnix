#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./ci/bootstrap-workstation-venv.sh [--venv <path>] [--cache-dir <path>]

Examples:
  ./ci/bootstrap-workstation-venv.sh
  ./ci/bootstrap-workstation-venv.sh --venv workstation/.venv
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/workstation/.venv"
CACHE_DIR="${UV_CACHE_DIR:-$ROOT_DIR/workstation/.uv-cache}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --venv)
      VENV_DIR="${2:?missing value for --venv}"
      shift 2
      ;;
    --cache-dir)
      CACHE_DIR="${2:?missing value for --cache-dir}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required to bootstrap the repo-local workstation virtualenv" >&2
  exit 1
fi

mkdir -p "$CACHE_DIR"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Creating virtualenv at $VENV_DIR"
  UV_CACHE_DIR="$CACHE_DIR" uv venv "$VENV_DIR"
fi

echo "Installing ansible and proxnix-workstation into $VENV_DIR"
UV_CACHE_DIR="$CACHE_DIR" uv pip install \
  --python "$VENV_DIR/bin/python" \
  ansible \
  -e "$ROOT_DIR/workstation/cli"

cat <<EOF

Repo-local workstation environment is ready.
Use these wrappers:
- $ROOT_DIR/workstation/cli/bin/proxnix
- $ROOT_DIR/workstation/.venv/bin/ansible-playbook
EOF
