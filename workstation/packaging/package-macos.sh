#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/package-common.sh"

[[ "$(uname -s)" == "Darwin" ]] || die "package-macos.sh must run on macOS"

prepare_dirs

stage_root="${BUILD_DIR}/macos/stage"
artifact="${DIST_DIR}/proxnix-manager-${VERSION}-macos-${ARCH}.dmg"
swift_package_dir="${WORKSTATION_DIR}/apps/ProxnixManager"
swift_executable="${swift_package_dir}/.build/release/ProxnixManager"

rm -rf "$stage_root"
mkdir -p "$stage_root"

ln -s /Applications "${stage_root}/Applications"

swift build -c release --package-path "$swift_package_dir"

create_info_plist() {
  local destination="$1"
  local bundle_name="$2"
  local bundle_identifier="$3"
  local executable_name="$4"

  cat > "$destination" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>${executable_name}</string>
  <key>CFBundleIdentifier</key>
  <string>${bundle_identifier}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>${bundle_name}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${VERSION}</string>
  <key>CFBundleVersion</key>
  <string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF
}

create_swift_app_bundle() {
  local bundle_dir="$1"

  mkdir -p "${bundle_dir}/Contents/MacOS" "${bundle_dir}/Contents/Resources"
  install -m 755 "$swift_executable" "${bundle_dir}/Contents/MacOS/ProxnixManager"
  install_workstation_bin "${bundle_dir}/Contents/Resources/bin"
  install_workstation_python_runtime "${bundle_dir}/Contents/Resources"
  write_packaged_cli_wrappers "${bundle_dir}/Contents/Resources/bin" "${bundle_dir}/Contents/Resources"
  create_info_plist \
    "${bundle_dir}/Contents/Info.plist" \
    "Proxnix Manager" \
    "org.proxnix.workstation.manager" \
    "ProxnixManager"
}

create_swift_app_bundle "${stage_root}/Proxnix Manager.app"

hdiutil create \
  -volname "Proxnix Manager" \
  -srcfolder "$stage_root" \
  -ov \
  -format UDZO \
  "$artifact" >/dev/null

printf '%s\n' "$artifact"
