#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./ci/install-workstation.sh [--version <version>] [--python <python-bin>]

Examples:
  ./ci/install-workstation.sh
  ./ci/install-workstation.sh --version 1.2.3
  ./ci/install-workstation.sh --python python3.12
EOF
}

python_bin="${PYTHON:-python3}"
version=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      version="${2:?missing value for --version}"
      shift 2
      ;;
    --python)
      python_bin="${2:?missing value for --python}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

package_spec="proxnix-workstation"
if [[ -n "$version" ]]; then
  package_spec="${package_spec}==${version}"
fi

echo "Installing ${package_spec} with ${python_bin}"
"$python_bin" -m pip install --user --upgrade "$package_spec"

cat <<'EOF'

Workstation runtime requirements still need to be available on the machine:
- sops
- ssh
- rsync
EOF
