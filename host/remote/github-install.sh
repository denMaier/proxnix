#!/bin/bash
# github-install.sh — curl-friendly wrapper for proxnix install.sh
#
# Intended usage:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/github-install.sh)" -- [install.sh args...]
#
# Override the defaults at runtime with:
#   PROXNIX_REPO_ARCHIVE_URL=...
# or:
#   PROXNIX_REPO_OWNER=... PROXNIX_REPO_NAME=... PROXNIX_REPO_BRANCH=...

set -euo pipefail

SELF_NAME="${0##*/}"

: "${PROXNIX_REPO_HOST:=https://github.com}"
: "${PROXNIX_REPO_OWNER:=denMaier}"
: "${PROXNIX_REPO_NAME:=proxnix}"
: "${PROXNIX_REPO_BRANCH:=main}"
: "${PROXNIX_REPO_ARCHIVE_URL:=}"

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

die() {
  echo "ERROR: $*" >&2
  exit 1
}

usage() {
  printf '%s\n' \
    "Usage:" \
    "  ${SELF_NAME} [install.sh args...]" \
    "" \
    "Examples:" \
    "  bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/github-install.sh)\"" \
    "  bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/github-install.sh)\" -- --dry-run" \
    "" \
    "Repository overrides:" \
    "  PROXNIX_REPO_ARCHIVE_URL=https://github.com/<owner>/<repo>/archive/refs/heads/<branch>.tar.gz" \
    "  PROXNIX_REPO_OWNER=<owner> PROXNIX_REPO_NAME=<repo> PROXNIX_REPO_BRANCH=<branch>"
}

resolve_archive_url() {
  if [[ -n "$PROXNIX_REPO_ARCHIVE_URL" ]]; then
    printf '%s\n' "$PROXNIX_REPO_ARCHIVE_URL"
    return
  fi

  printf '%s/%s/%s/archive/refs/heads/%s.tar.gz\n' \
    "$PROXNIX_REPO_HOST" \
    "$PROXNIX_REPO_OWNER" \
    "$PROXNIX_REPO_NAME" \
    "$PROXNIX_REPO_BRANCH"
}

main() {
  local archive_url tmp_dir repo_dir archive_path
  local bash_bin curl_bin mkdir_bin mktemp_bin rm_bin tar_bin

  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
  fi

  bash_bin="$(pick_bin /bin/bash /usr/bin/bash)" || die "bash not found"
  curl_bin="$(pick_bin /usr/bin/curl /bin/curl)" || die "curl not found"
  mkdir_bin="$(pick_bin /bin/mkdir /usr/bin/mkdir)" || die "mkdir not found"
  mktemp_bin="$(pick_bin /usr/bin/mktemp /bin/mktemp)" || die "mktemp not found"
  rm_bin="$(pick_bin /bin/rm /usr/bin/rm)" || die "rm not found"
  tar_bin="$(pick_bin /usr/bin/tar /bin/tar)" || die "tar not found"

  archive_url="$(resolve_archive_url)"
  tmp_dir="$("$mktemp_bin" -d)"
  repo_dir="${tmp_dir}/repo"
  archive_path="${tmp_dir}/repo.tar.gz"

  cleanup() {
    "$rm_bin" -rf "$tmp_dir"
  }
  trap cleanup EXIT

  echo "Downloading proxnix repo archive..."
  "$mkdir_bin" -p "$repo_dir"
  "$curl_bin" -fsSL "$archive_url" -o "$archive_path"
  "$tar_bin" -xzf "$archive_path" -C "$repo_dir" --strip-components=1

  exec "$bash_bin" "${repo_dir}/host/install.sh" "$@"
}

main "$@"
