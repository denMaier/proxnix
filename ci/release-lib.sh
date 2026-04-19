#!/usr/bin/env bash

set -euo pipefail

release_die() {
  echo "error: $*" >&2
  exit 1
}

is_release_bump_kind() {
  case "${1:-}" in
    major|minor|patch) return 0 ;;
    *) return 1 ;;
  esac
}

is_release_version() {
  [[ "${1:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]
}

validate_release_version() {
  local version="${1:-}"
  is_release_version "$version" || release_die "invalid release version '${version}'"
  printf '%s\n' "$version"
}

bump_release_version() {
  local current="${1:-}" kind="${2:-}"
  local major minor patch

  is_release_bump_kind "$kind" || release_die "invalid bump kind '${kind}' (expected major, minor, or patch)"
  current="$(validate_release_version "$current")"
  if [[ ! "$current" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)([.-][0-9A-Za-z.-]+)?$ ]]; then
    release_die "invalid release version '${current}'"
  fi
  major="${BASH_REMATCH[1]}"
  minor="${BASH_REMATCH[2]}"
  patch="${BASH_REMATCH[3]}"

  case "$kind" in
    major)
      major=$((major + 1))
      minor=0
      patch=0
      ;;
    minor)
      minor=$((minor + 1))
      patch=0
      ;;
    patch)
      patch=$((patch + 1))
      ;;
  esac

  printf '%s.%s.%s\n' "$major" "$minor" "$patch"
}

normalize_release_tag() {
  local raw="${1:-}"
  [[ -n "$raw" ]] || release_die "release tag is required"

  if [[ "$raw" == v* ]]; then
    printf '%s\n' "$raw"
  else
    printf 'v%s\n' "$raw"
  fi
}

is_release_tag() {
  local tag="${1:-}"
  [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]
}

validate_release_tag() {
  local tag
  tag="$(normalize_release_tag "${1:-}")"
  is_release_tag "$tag" || release_die "invalid release tag '${tag}' (expected vMAJOR.MINOR.PATCH or prerelease suffix)"
  printf '%s\n' "$tag"
}

release_version_from_tag() {
  local tag
  tag="$(validate_release_tag "${1:-}")"
  printf '%s\n' "${tag#v}"
}

tag_object_type() {
  local tag="${1:-}"
  git for-each-ref "refs/tags/${tag}" --format='%(objecttype)'
}

require_annotated_tag() {
  local tag="${1:-}"
  [[ "$(tag_object_type "$tag")" == "tag" ]] || release_die "tag '${tag}' must be annotated"
}

tag_target_commit() {
  local tag="${1:-}"
  git rev-list -n 1 "$tag"
}

ensure_clean_worktree() {
  git diff --quiet --cached || release_die "index has staged changes"
  git diff --quiet || release_die "worktree has unstaged changes"
}

default_release_remote() {
  printf '%s\n' "${RELEASE_REMOTE:-origin}"
}

ensure_branch_exists() {
  local branch="${1:-}"
  git show-ref --verify --quiet "refs/heads/${branch}"
}

ensure_commit_on_release_branch() {
  local tag="${1:-}" branch="${2:-main}" commit
  commit="$(tag_target_commit "$tag")"

  if ! ensure_branch_exists "$branch"; then
    return 0
  fi

  git merge-base --is-ancestor "$commit" "refs/heads/${branch}" \
    || release_die "tag '${tag}' does not point to a commit reachable from '${branch}'"
}
