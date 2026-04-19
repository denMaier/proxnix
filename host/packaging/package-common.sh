#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST_DIR="${ROOT_DIR}/host"
PACKAGING_DIR="${HOST_DIR}/packaging"
DIST_DIR="${DIST_DIR:-${ROOT_DIR}/dist}"
BUILD_DIR="${BUILD_DIR:-${ROOT_DIR}/.tmp-host-packaging}"
VERSION="${VERSION:-$(git -C "${ROOT_DIR}" rev-parse --short=12 HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
PACKAGE_NAME="${PACKAGE_NAME:-proxnix-host}"

readonly ROOT_DIR HOST_DIR PACKAGING_DIR DIST_DIR BUILD_DIR VERSION PACKAGE_NAME

HOST_PACKAGE_FILES=(
  "lxc/config/nixos.common.conf:/usr/share/lxc/config/nixos.common.conf:0644"
  "lxc/config/nixos.userns.conf:/usr/share/lxc/config/nixos.userns.conf:0644"
  "lxc/hooks/nixos-proxnix-prestart:/usr/share/lxc/hooks/nixos-proxnix-prestart:0755"
  "lxc/hooks/nixos-proxnix-mount:/usr/share/lxc/hooks/nixos-proxnix-mount:0755"
  "lxc/hooks/nixos-proxnix-poststop:/usr/share/lxc/hooks/nixos-proxnix-poststop:0755"
  "pve-conf-to-nix.py:/usr/local/lib/proxnix/pve-conf-to-nix.py:0755"
  "lxc/hooks/nixos-proxnix-common.sh:/usr/local/lib/proxnix/nixos-proxnix-common.sh:0644"
  "proxnix-secrets-guest:/usr/local/lib/proxnix/proxnix-secrets-guest:0755"
  "proxnix-doctor:/usr/local/sbin/proxnix-doctor:0755"
  "proxnix-create-lxc:/usr/local/sbin/proxnix-create-lxc:0755"
  "systemd/proxnix-gc.service:/etc/systemd/system/proxnix-gc.service:0644"
  "systemd/proxnix-gc.timer:/etc/systemd/system/proxnix-gc.timer:0644"
  "base.nix:/var/lib/proxnix/base.nix:0644"
  "common.nix:/var/lib/proxnix/common.nix:0644"
  "security-policy.nix:/var/lib/proxnix/security-policy.nix:0644"
  "configuration.nix:/var/lib/proxnix/configuration.nix:0644"
)

die() {
  echo "error: $*" >&2
  exit 1
}

prepare_dirs() {
  mkdir -p "$DIST_DIR" "$BUILD_DIR"
}

deb_version() {
  local raw version

  raw="${DEB_VERSION:-$VERSION}"
  case "$raw" in
    v[0-9]*)
      version="${raw#v}"
      ;;
    sha-*)
      version="0+git.${raw#sha-}"
      ;;
    [0-9]*)
      version="$raw"
      ;;
    *)
      version="0+${raw}"
      ;;
  esac

  version="${version//-/.}"
  version="${version//_/.}"
  printf '%s\n' "$version"
}

deb_architecture() {
  if [[ -n "${DEB_ARCH:-}" ]]; then
    printf '%s\n' "$DEB_ARCH"
    return
  fi

  if command -v dpkg-architecture >/dev/null 2>&1; then
    dpkg-architecture -qDEB_HOST_ARCH
    return
  fi

  case "$(uname -m)" in
    x86_64) printf 'amd64\n' ;;
    aarch64|arm64) printf 'arm64\n' ;;
    armv7l) printf 'armhf\n' ;;
    i386|i686) printf 'i386\n' ;;
    *) die "unsupported architecture: $(uname -m); set DEB_ARCH explicitly" ;;
  esac
}

install_host_payload() {
  local stage_root="$1"
  local spec src rel_src dest mode

  install -d -m 0755 \
    "${stage_root}/usr/share/lxc/config" \
    "${stage_root}/usr/share/lxc/hooks" \
    "${stage_root}/usr/local/lib/proxnix" \
    "${stage_root}/usr/local/sbin" \
    "${stage_root}/etc/systemd/system" \
    "${stage_root}/usr/share/doc/${PACKAGE_NAME}"
  install -d -m 0755 \
    "${stage_root}/var/lib/proxnix" \
    "${stage_root}/var/lib/proxnix/containers"
  install -d -m 0700 \
    "${stage_root}/var/lib/proxnix/private" \
    "${stage_root}/var/lib/proxnix/private/shared" \
    "${stage_root}/var/lib/proxnix/private/containers" \
    "${stage_root}/etc/proxnix"

  for spec in "${HOST_PACKAGE_FILES[@]}"; do
    IFS=':' read -r rel_src dest mode <<< "$spec"
    src="${HOST_DIR}/${rel_src}"
    install -d -m 0755 "$(dirname "${stage_root}${dest}")"
    install -m "$mode" "$src" "${stage_root}${dest}"
  done

  cat > "${stage_root}/usr/share/doc/${PACKAGE_NAME}/README.Debian" <<EOF
proxnix host package
====================

This package installs the proxnix Proxmox-host runtime:

- LXC config fragments and hooks
- proxnix host helpers
- shared managed NixOS baseline files
- the proxnix GC systemd timer

Published relay data remains outside the package payload:

- /var/lib/proxnix/site.nix
- /var/lib/proxnix/containers/
- /var/lib/proxnix/private/
- /etc/proxnix/host_relay_identity

Remove the package with:

  apt remove ${PACKAGE_NAME}

or:

  dpkg -r ${PACKAGE_NAME}
EOF
}
