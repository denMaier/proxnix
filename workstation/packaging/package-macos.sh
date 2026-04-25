#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/package-common.sh"

[[ "$(uname -s)" == "Darwin" ]] || die "package-macos.sh must run on macOS"

prepare_dirs

artifact="${DIST_DIR}/proxnix-manager-${VERSION}-macos-${ARCH}.dmg"
app_dir="${WORKSTATION_DIR}/manager"
electrobun_artifact="${app_dir}/artifacts/stable-macos-${ARCH}-ProxnixManager.dmg"

command -v bun >/dev/null 2>&1 || die "bun is required to package the Electrobun app"
[[ -d "${app_dir}/node_modules" ]] || die "missing ${app_dir}/node_modules; run bun install in ${app_dir} first"

rm -f "$artifact"
(
  cd "$app_dir"
  env VERSION="$VERSION" bun run build
)

[[ -f "$electrobun_artifact" ]] || die "expected Electrobun artifact not found: ${electrobun_artifact}"
cp "$electrobun_artifact" "$artifact"

printf '%s\n' "$artifact"
