#!/bin/bash
# install-host-package.sh — helper-script style installer for the published proxnix-host .deb
#
# Examples:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/install-host-package.sh)"
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/install-host-package.sh)" -- --version 1.2.3
#   PROXNIX_PACKAGE_OWNER=myorg bash -c "$(curl -fsSL .../install-host-package.sh)"
#
# This is the canonical host bootstrap path. The user-facing script is fetched
# from GitHub, and the underlying `.deb` artifacts are resolved from GitHub
# release assets.

set -euo pipefail

SELF_NAME="${0##*/}"

: "${PROXNIX_PACKAGE_SERVER:=https://api.github.com}"
: "${PROXNIX_PACKAGE_OWNER:=denMaier}"
: "${PROXNIX_PACKAGE_REPO:=proxnix}"

VERSION=""
DRY_RUN=0

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
  PROXNIX_PACKAGE_REPO
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

resolve_release_json() {
  local release_url="$1"
  "$curl_bin" -fsSL \
    -H "Accept: application/vnd.github+json" \
    "$release_url"
}

parse_release_json() {
  local release_json="$1" arch="$2"
  python3 - <<'PY' "$release_json" "$arch"
import json
import re
import sys

payload = json.loads(sys.argv[1])
arch = sys.argv[2]
assets = payload.get("assets", [])
deb_pattern = re.compile(rf"^proxnix-host_[^/]+_{re.escape(arch)}\.deb$")

deb_asset = None
sha_asset = None
for asset in assets:
    name = asset.get("name", "")
    if deb_pattern.match(name):
        deb_asset = asset
    elif name == "SHA256SUMS-host.txt":
        sha_asset = asset

if deb_asset is None:
    raise SystemExit(f"error: no proxnix-host release asset found for architecture {arch}")

print(f"TAG_NAME={payload['tag_name']}")
print(f"FILENAME={deb_asset['name']}")
print(f"PACKAGE_URL={deb_asset['browser_download_url']}")
if sha_asset is not None:
    print(f"CHECKSUM_URL={sha_asset['browser_download_url']}")
PY
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
  release_url="${PROXNIX_PACKAGE_SERVER%/}/repos/${PROXNIX_PACKAGE_OWNER}/${PROXNIX_PACKAGE_REPO}/releases/latest"
else
  if [[ "$VERSION" == v* ]]; then
    VERSION_TAG="$VERSION"
  else
    VERSION_TAG="v${VERSION}"
  fi
  release_url="${PROXNIX_PACKAGE_SERVER%/}/repos/${PROXNIX_PACKAGE_OWNER}/${PROXNIX_PACKAGE_REPO}/releases/tags/${VERSION_TAG}"
fi

release_json="$(resolve_release_json "$release_url")" || die "failed to fetch release metadata from ${release_url}"
CHECKSUM_URL=""
while IFS='=' read -r key value; do
  case "$key" in
    TAG_NAME) TAG_NAME="$value" ;;
    FILENAME) filename="$value" ;;
    PACKAGE_URL) package_url="$value" ;;
    CHECKSUM_URL) CHECKSUM_URL="$value" ;;
  esac
done < <(parse_release_json "$release_json" "$arch")

[[ -n "${TAG_NAME:-}" ]] || die "release metadata missing tag name"
[[ -n "${filename:-}" ]] || die "release metadata missing package filename"
[[ -n "${package_url:-}" ]] || die "release metadata missing package url"

tmp_dir="$("$mktemp_bin" -d)"
deb_path="${tmp_dir}/${filename}"
checksum_path="${tmp_dir}/SHA256SUMS-host.txt"

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

if [[ -n "${CHECKSUM_URL:-}" && -n "$sha256sum_bin" ]]; then
  "$curl_bin" -fsSL "$CHECKSUM_URL" -o "$checksum_path" || die "failed to download ${CHECKSUM_URL}"
  (
    cd "$tmp_dir"
    grep " ${filename}$" "$checksum_path" | "$sha256sum_bin" --check --status
  ) || die "sha256 mismatch for ${filename}"
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

echo "Installed proxnix-host ${TAG_NAME#v}"
