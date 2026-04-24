#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
# shellcheck source=/dev/null
source "${ROOT_DIR}/workstation/packaging/package-common.sh"

bundle_dir="${1:-${ELECTROBUN_WRAPPER_BUNDLE_PATH:-}}"
[[ -n "$bundle_dir" ]] || die "bundle path is required"
[[ -d "$bundle_dir" ]] || die "wrapper bundle not found: ${bundle_dir}"

resources_dir="${bundle_dir}/Contents/Resources"
bin_dir="${resources_dir}/bin"

rm -rf "${bin_dir}" "${resources_dir}/lib"
mkdir -p "${bin_dir}"

install_workstation_bin "${bin_dir}"
install_workstation_python_runtime "${resources_dir}"
write_packaged_cli_wrappers "${bin_dir}" "${resources_dir}"
write_runtime_readme "${resources_dir}" "electrobun-app"
