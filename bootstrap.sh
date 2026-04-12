#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "NOTE: ./bootstrap.sh is deprecated; use ./bootstrap-guest-secrets.sh instead." >&2
exec "${SCRIPT_DIR}/bootstrap-guest-secrets.sh" "$@"
