#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/release-lib.sh"

usage() {
  cat <<'EOF'
Usage:
  ./ci/release.sh <major|minor|patch>
  ./ci/release.sh --version <version>
    [--branch <name>] [--remote <name>] [--message <text>] [--tag-message <text>] [--no-push] [--dry-run]

Examples:
  ./ci/release.sh patch
  ./ci/release.sh minor
  ./ci/release.sh --version 1.2.3-rc1
EOF
}

release_input=""
explicit_version=""
release_branch="${RELEASE_BRANCH:-main}"
release_remote="$(default_release_remote)"
commit_message=""
tag_message=""
push_after_release=1
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)
      release_branch="${2:?missing value for --branch}"
      shift 2
      ;;
    --version)
      explicit_version="${2:?missing value for --version}"
      shift 2
      ;;
    --remote)
      release_remote="${2:?missing value for --remote}"
      shift 2
      ;;
    --message)
      commit_message="${2:?missing value for --message}"
      shift 2
      ;;
    --tag-message)
      tag_message="${2:?missing value for --tag-message}"
      shift 2
      ;;
    --no-push)
      push_after_release=0
      shift
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
      [[ -z "$release_input" ]] || release_die "only one release target may be provided"
      release_input="$1"
      shift
      ;;
  esac
done

if [[ -n "$explicit_version" && -n "$release_input" ]]; then
  release_die "use either a bump kind or --version, not both"
fi

release_input="${explicit_version:-$release_input}"
[[ -n "$release_input" ]] || release_die "release target is required"

current_version="$("${SCRIPT_DIR}/project-version.sh")"
if is_release_bump_kind "$release_input"; then
  release_version="$(bump_release_version "$current_version" "$release_input")"
else
  release_version="$(validate_release_version "$release_input")"
fi

tag="$(validate_release_tag "$release_version")"
commit_message="${commit_message:-Release ${tag}}"
tag_message="${tag_message:-Release ${tag}}"

git rev-parse --verify HEAD >/dev/null 2>&1 || release_die "not on a valid commit"
if [[ $dry_run -eq 0 ]]; then
  ensure_clean_worktree
else
  if ! git diff --quiet --cached || ! git diff --quiet; then
    echo "note: worktree is dirty; dry-run continues without enforcing clean-worktree checks" >&2
  fi
fi

if ensure_branch_exists "$release_branch"; then
  git merge-base --is-ancestor HEAD "refs/heads/${release_branch}" \
    || release_die "HEAD is not reachable from '${release_branch}'"
fi

echo "Updating VERSION and workstation/pyproject.toml to ${release_version}"
if [[ $dry_run -eq 0 ]]; then
  "${SCRIPT_DIR}/set-version.sh" "$release_version"
fi

echo "Creating release commit: ${commit_message}"
if [[ $dry_run -eq 0 ]]; then
  git add VERSION workstation/pyproject.toml
  if ! git diff --cached --quiet; then
    git commit -m "$commit_message"
  else
    echo "Version files already at ${release_version}; skipping commit"
  fi
fi

echo "Creating release tag ${tag}"
if [[ $dry_run -eq 1 ]]; then
  if git show-ref --verify --quiet "refs/tags/${tag}"; then
    release_die "tag '${tag}' already exists"
  fi

  head_commit="$(git rev-parse HEAD)"
  echo "Creating annotated release tag ${tag} on ${head_commit}"
  if [[ $push_after_release -eq 1 ]]; then
    echo "Pushing HEAD and ${tag} to ${release_remote}"
  else
    echo "Tag created locally. Push it with:"
    echo "  git push ${release_remote} HEAD"
    echo "  git push ${release_remote} refs/tags/${tag}"
  fi
else
  tag_args=("$release_version" "--branch" "$release_branch" "--remote" "$release_remote" "--message" "$tag_message")
  if [[ $push_after_release -eq 1 ]]; then
    tag_args+=("--push")
  fi

  "${SCRIPT_DIR}/release-tag.sh" "${tag_args[@]}"
fi
