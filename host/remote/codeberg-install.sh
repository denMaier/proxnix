#!/bin/bash
# codeberg-install.sh — deprecated compatibility wrapper
#
# Prefer:
#   https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/github-install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/github-install.sh" "$@"
