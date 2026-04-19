#!/usr/bin/env bash

set -euo pipefail

if [[ "${GITHUB_REF_TYPE:-}" == "tag" && -n "${GITHUB_REF_NAME:-}" ]]; then
  printf '%s\n' "$GITHUB_REF_NAME"
  exit 0
fi

if [[ "${GITHUB_REF:-}" == refs/tags/* ]]; then
  printf '%s\n' "${GITHUB_REF#refs/tags/}"
  exit 0
fi

printf 'sha-%s\n' "$(git rev-parse --short=12 HEAD)"
