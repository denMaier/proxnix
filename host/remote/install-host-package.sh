#!/bin/bash
# install-host-package.sh — helper-script style installer for the published proxnix-host .deb
#
# Examples:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/install-host-package.sh)"
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/install-host-package.sh)" -- --version 1.2.3
#   PROXNIX_PACKAGE_OWNER=myorg bash -c "$(curl -fsSL .../install-host-package.sh)"
#
# This is the canonical host bootstrap path. The user-facing script is fetched
# from GitHub, while the underlying .deb artifacts are still downloaded from
# the published package registry configured below.

set -euo pipefail

SELF_NAME="${0##*/}"

: "${PROXNIX_PACKAGE_SERVER:=https://codeberg.org}"
: "${PROXNIX_PACKAGE_OWNER:=maieretal}"
: "${PROXNIX_PACKAGE_NAME:=proxnix-host-deb}"
: "${PROXNIX_PACKAGE_META_NAME:=proxnix-host-meta}"

VERSION=""
DRY_RUN=0
REGISTRY_VERSION=""

die() {
  echo "ERROR: $*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage:
  ${SELF_NAME} [--version <version>] [--dry-run]

Environment overrides:
  PROXNIX_PACKAGE_SERVER
  PROXNIX_PACKAGE_OWNER
  PROXNIX_PACKAGE_NAME
  PROXNIX_PACKAGE_META_NAME
EOF
}

pick_bin() {
  local candidate
  for candidate in "$@"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

map_arch() {
  local arch="${1:-}"
  case "$arch" in
    amd64|arm64|armhf|i386) printf '%s\n' "$arch" ;;
    x86_64) printf 'amd64\n' ;;
    aarch64) printf 'arm64\n' ;;
    armv7l) printf 'armhf\n' ;;
    i386|i686) printf 'i386\n' ;;
    *) die "unsupported architecture: ${arch}" ;;
  esac
}

resolve_arch() {
  local dpkg_bin uname_bin arch
  dpkg_bin="$(pick_bin /usr/bin/dpkg /bin/dpkg || true)"
  if [[ -n "$dpkg_bin" ]]; then
    arch="$("$dpkg_bin" --print-architecture 2>/dev/null || true)"
    if [[ -n "$arch" ]]; then
      map_arch "$arch"
      return
    fi
  fi

  uname_bin="$(pick_bin /usr/bin/uname /bin/uname)" || die "uname not found"
  arch="$("$uname_bin" -m)"
  map_arch "$arch"
}

parse_latest_metadata() {
  local content="$1" key value line
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      VERSION) LATEST_VERSION="$value" ;;
      FILENAME) LATEST_FILENAME="$value" ;;
      SHA256) LATEST_SHA256="$value" ;;
    esac
  done <<< "$content"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:?missing value for --version}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

curl_bin="$(pick_bin /usr/bin/curl /bin/curl)" || die "curl not found"
mktemp_bin="$(pick_bin /usr/bin/mktemp /bin/mktemp)" || die "mktemp not found"
rm_bin="$(pick_bin /bin/rm /usr/bin/rm)" || die "rm not found"
sha256sum_bin="$(pick_bin /usr/bin/sha256sum /bin/sha256sum || true)"
apt_bin="$(pick_bin /usr/bin/apt /bin/apt || true)"
dpkg_bin="$(pick_bin /usr/bin/dpkg /bin/dpkg || true)"
apt_get_bin="$(pick_bin /usr/bin/apt-get /bin/apt-get || true)"

arch="$(resolve_arch)"

if [[ -z "$VERSION" ]]; then
  latest_url="${PROXNIX_PACKAGE_SERVER%/}/api/packages/${PROXNIX_PACKAGE_OWNER}/generic/${PROXNIX_PACKAGE_META_NAME}/latest/proxnix-host-latest.env"
  latest_content="$("$curl_bin" -fsSL "$latest_url")" || die "failed to fetch latest release metadata from ${latest_url}"
  LATEST_VERSION=""
  LATEST_FILENAME=""
  LATEST_SHA256=""
  parse_latest_metadata "$latest_content"
  [[ -n "$LATEST_VERSION" ]] || die "latest metadata is missing VERSION"
  VERSION="$LATEST_VERSION"
  REGISTRY_VERSION="$LATEST_VERSION"
  filename="${LATEST_FILENAME:-proxnix-host_${VERSION}_${arch}.deb}"
  expected_sha256="${LATEST_SHA256:-}"
else
  if [[ "$VERSION" == sha-* || "$VERSION" == v* ]]; then
    REGISTRY_VERSION="$VERSION"
  else
    REGISTRY_VERSION="v${VERSION}"
  fi
  filename="proxnix-host_${VERSION#v}_${arch}.deb"
  expected_sha256=""
fi

package_url="${PROXNIX_PACKAGE_SERVER%/}/api/packages/${PROXNIX_PACKAGE_OWNER}/generic/${PROXNIX_PACKAGE_NAME}/${REGISTRY_VERSION}/${filename}"
tmp_dir="$("$mktemp_bin" -d)"
deb_path="${tmp_dir}/${filename}"

cleanup() {
  "$rm_bin" -rf "$tmp_dir"
}
trap cleanup EXIT

echo "Installing ${filename} from ${package_url}"
if [[ $DRY_RUN -eq 1 ]]; then
  exit 0
fi

[[ "$(id -u)" -eq 0 ]] || die "must be run as root"

"$curl_bin" -fsSL "$package_url" -o "$deb_path" || die "failed to download ${package_url}"

if [[ -n "$expected_sha256" && -n "$sha256sum_bin" ]]; then
  actual_sha256="$("$sha256sum_bin" "$deb_path" | awk '{print $1}')"
  [[ "$actual_sha256" == "$expected_sha256" ]] || die "sha256 mismatch for ${filename}"
fi

if [[ -n "$apt_bin" ]]; then
  "$apt_bin" install -y "$deb_path"
elif [[ -n "$dpkg_bin" ]]; then
  "$dpkg_bin" -i "$deb_path"
  if [[ -n "$apt_get_bin" ]]; then
    "$apt_get_bin" install -f -y
  fi
else
  die "neither apt nor dpkg found"
fi

echo "Installed proxnix-host ${VERSION#v}"
