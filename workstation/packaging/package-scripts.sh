#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/package-common.sh"

prepare_dirs

stage_root="${BUILD_DIR}/scripts/proxnix-workstation-scripts-${VERSION}"
artifact="${DIST_DIR}/proxnix-workstation-scripts-${VERSION}.tar.gz"

rm -rf "$stage_root"
mkdir -p "$stage_root"

install_workstation_bin "${stage_root}/bin"
install_workstation_python_runtime "$stage_root"
write_packaged_cli_wrappers "${stage_root}/bin" "$stage_root"
write_runtime_readme "$stage_root" "scripts"

tar -C "$(dirname "$stage_root")" -czf "$artifact" "$(basename "$stage_root")"
printf '%s\n' "$artifact"
