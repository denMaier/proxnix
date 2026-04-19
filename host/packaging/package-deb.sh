#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/package-common.sh"

command -v dpkg-deb >/dev/null 2>&1 || die "dpkg-deb not found"

prepare_dirs

deb_arch="$(deb_architecture)"
package_version="$(deb_version)"
stage_root="${BUILD_DIR}/deb/${PACKAGE_NAME}_${package_version}_${deb_arch}"
artifact="${DIST_DIR}/${PACKAGE_NAME}_${package_version}_${deb_arch}.deb"
debian_dir="${stage_root}/DEBIAN"

rm -rf "$stage_root"
mkdir -p "$debian_dir"

install_host_payload "$stage_root"

cat > "${debian_dir}/control" <<EOF
Package: ${PACKAGE_NAME}
Version: ${package_version}
Section: admin
Priority: optional
Architecture: ${deb_arch}
Maintainer: Denis Maier
Depends: bash, python3, sops
Description: proxnix host runtime for Proxmox-managed NixOS LXCs
 Installs the proxnix host-side LXC hooks, helper commands, shared managed
 NixOS baseline files, and the proxnix GC timer.
EOF

install -m 0755 "${SCRIPT_DIR}/debian/postinst" "${debian_dir}/postinst"
install -m 0755 "${SCRIPT_DIR}/debian/prerm" "${debian_dir}/prerm"
install -m 0755 "${SCRIPT_DIR}/debian/postrm" "${debian_dir}/postrm"

dpkg-deb --build "$stage_root" "$artifact" >/dev/null
printf '%s\n' "$artifact"
