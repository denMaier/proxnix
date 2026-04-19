#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE="${ROOT_DIR}/packaging/homebrew/Casks/proxnix-manager.rb.template"

usage() {
  cat <<'EOF'
Usage:
  ./ci/render-homebrew-cask.sh [--version <version>] [--sha256-arm64 <sha256>] [--output <path>]
                               [--repo-host <url>] [--repo-owner <owner>] [--repo-name <name>]

Examples:
  ./ci/render-homebrew-cask.sh --version 0.1.0
  ./ci/render-homebrew-cask.sh --version 0.1.0 --output /tmp/proxnix-manager.rb

If the SHA256 value is omitted, the script downloads the arm64 DMG from the
GitHub release and computes it.
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

version="$(<"${ROOT_DIR}/VERSION")"
sha256_arm64=""
output=""
repo_host="${PROXNIX_REPO_HOST:-https://github.com}"
repo_owner="${PROXNIX_REPO_OWNER:-denMaier}"
repo_name="${PROXNIX_REPO_NAME:-proxnix}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      version="${2:?missing value for --version}"
      shift 2
      ;;
    --sha256-arm64)
      sha256_arm64="${2:?missing value for --sha256-arm64}"
      shift 2
      ;;
    --output)
      output="${2:?missing value for --output}"
      shift 2
      ;;
    --repo-host)
      repo_host="${2:?missing value for --repo-host}"
      shift 2
      ;;
    --repo-owner)
      repo_owner="${2:?missing value for --repo-owner}"
      shift 2
      ;;
    --repo-name)
      repo_name="${2:?missing value for --repo-name}"
      shift 2
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

[[ -f "$TEMPLATE" ]] || die "template not found: $TEMPLATE"

version="${version#v}"
tag="v${version}"
download_base="${repo_host%/}/${repo_owner}/${repo_name}/releases/download/${tag}"
url_arm64="${download_base}/proxnix-manager-${version}-macos-arm64.dmg"

if [[ -z "$sha256_arm64" ]]; then
  command -v curl >/dev/null 2>&1 || die "curl not found; pass --sha256-arm64 explicitly"
  if command -v shasum >/dev/null 2>&1; then
    sha_cmd=(shasum -a 256)
  elif command -v sha256sum >/dev/null 2>&1; then
    sha_cmd=(sha256sum)
  else
    die "no sha256 tool found; pass --sha256-arm64 explicitly"
  fi

  arm64_tmp="$(mktemp)"
  trap 'rm -f "$arm64_tmp"' EXIT

  curl -fsSL "$url_arm64" -o "$arm64_tmp"
  sha256_arm64="$("${sha_cmd[@]}" "$arm64_tmp" | awk '{print $1}')"
fi

rendered="$(
  sed \
    -e "s|{{VERSION}}|${version}|g" \
    -e "s|{{SHA256_ARM64}}|${sha256_arm64}|g" \
    -e "s|{{DOWNLOAD_BASE}}|${download_base}|g" \
    -e "s|{{HOMEPAGE}}|${repo_host%/}/${repo_owner}/${repo_name}|g" \
    "$TEMPLATE"
)"

if [[ -n "$output" ]]; then
  mkdir -p "$(dirname "$output")"
  printf '%s\n' "$rendered" > "$output"
  printf '%s\n' "$output"
else
  printf '%s\n' "$rendered"
fi
