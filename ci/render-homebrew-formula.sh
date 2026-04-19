#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE="${ROOT_DIR}/packaging/homebrew/Formula/proxnix-workstation.rb.template"

usage() {
  cat <<'EOF'
Usage:
  ./ci/render-homebrew-formula.sh [--version <version>] [--sha256 <sha256>] [--output <path>]
                                  [--repo-host <url>] [--repo-owner <owner>] [--repo-name <name>]

Examples:
  ./ci/render-homebrew-formula.sh --version 0.1.0
  ./ci/render-homebrew-formula.sh --version 0.1.0 --output /tmp/proxnix-workstation.rb

If --sha256 is omitted, the script downloads the release tarball and computes it.
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

version="$(<"${ROOT_DIR}/VERSION")"
sha256=""
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
    --sha256)
      sha256="${2:?missing value for --sha256}"
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
url="${repo_host%/}/${repo_owner}/${repo_name}/archive/${tag}.tar.gz"

if [[ -z "$sha256" ]]; then
  command -v curl >/dev/null 2>&1 || die "curl not found; pass --sha256 explicitly"
  if command -v shasum >/dev/null 2>&1; then
    sha_cmd=(shasum -a 256)
  elif command -v sha256sum >/dev/null 2>&1; then
    sha_cmd=(sha256sum)
  else
    die "no sha256 tool found; pass --sha256 explicitly"
  fi

  tmp_file="$(mktemp)"
  trap 'rm -f "$tmp_file"' EXIT
  curl -fsSL "$url" -o "$tmp_file"
  sha256="$("${sha_cmd[@]}" "$tmp_file" | awk '{print $1}')"
fi

rendered="$(
  sed \
    -e "s|{{VERSION}}|${version}|g" \
    -e "s|{{SHA256}}|${sha256}|g" \
    -e "s|{{URL}}|${url}|g" \
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
