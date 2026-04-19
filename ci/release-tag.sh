#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/release-lib.sh"

usage() {
  cat <<'EOF'
Usage:
  ./ci/release-tag.sh <version> [--push] [--branch <name>] [--remote <name>] [--message <text>] [--dry-run]

Examples:
  ./ci/release-tag.sh 1.2.3
  ./ci/release-tag.sh v1.2.3 --push
  ./ci/release-tag.sh 1.2.3-rc1 --push --message "Release candidate 1"

For the full version-bump + commit + tag flow, use:
  ./ci/release.sh <major|minor|patch>
  ./ci/release.sh --version <version>
EOF
}

tag=""
push_after_create=0
release_branch="${RELEASE_BRANCH:-main}"
release_remote="$(default_release_remote)"
release_message=""
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --push)
      push_after_create=1
      shift
      ;;
    --branch)
      release_branch="${2:?missing value for --branch}"
      shift 2
      ;;
    --remote)
      release_remote="${2:?missing value for --remote}"
      shift 2
      ;;
    --message)
      release_message="${2:?missing value for --message}"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    -*)
      release_die "unknown option: $1"
      ;;
    *)
      [[ -z "$tag" ]] || release_die "only one version/tag may be provided"
      tag="$1"
      shift
      ;;
  esac
done

tag="$(validate_release_tag "$tag")"
release_message="${release_message:-Release ${tag}}"
release_version="$(release_version_from_tag "$tag")"
project_version="$("${SCRIPT_DIR}/project-version.sh")"
workstation_version="$("${SCRIPT_DIR}/workstation-version.sh")"

git rev-parse --verify HEAD >/dev/null 2>&1 || release_die "not on a valid commit"
if [[ $dry_run -eq 0 ]]; then
  ensure_clean_worktree
else
  if ! git diff --quiet --cached || ! git diff --quiet; then
    echo "note: worktree is dirty; dry-run continues without enforcing clean-worktree checks" >&2
  fi
fi
[[ "$release_version" == "$project_version" ]] \
  || release_die "release tag ${tag} does not match VERSION ${project_version}"
[[ "$project_version" == "$workstation_version" ]] \
  || release_die "VERSION ${project_version} does not match workstation package version ${workstation_version}"

if git show-ref --verify --quiet "refs/tags/${tag}"; then
  release_die "tag '${tag}' already exists"
fi

head_commit="$(git rev-parse HEAD)"
if ensure_branch_exists "$release_branch"; then
  git merge-base --is-ancestor "$head_commit" "refs/heads/${release_branch}" \
    || release_die "HEAD is not reachable from '${release_branch}'"
fi

echo "Creating annotated release tag ${tag} on ${head_commit}"
if [[ $dry_run -eq 0 ]]; then
  git tag -a "$tag" -m "$release_message"
fi

if [[ $push_after_create -eq 1 ]]; then
  echo "Pushing HEAD and ${tag} to ${release_remote}"
  if [[ $dry_run -eq 0 ]]; then
    git push "$release_remote" HEAD
    git push "$release_remote" "refs/tags/${tag}"
  fi
else
  echo "Tag created locally. Push it with:"
  echo "  git push ${release_remote} HEAD"
  echo "  git push ${release_remote} refs/tags/${tag}"
fi
