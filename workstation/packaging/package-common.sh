#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKSTATION_DIR="${ROOT_DIR}/workstation"
DIST_DIR="${DIST_DIR:-${ROOT_DIR}/dist}"
BUILD_DIR="${BUILD_DIR:-${ROOT_DIR}/.tmp-workstation-packaging}"
VERSION="${VERSION:-$(git -C "${ROOT_DIR}" rev-parse --short=12 HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
ARCH="${ARCH:-$(uname -m)}"

readonly ROOT_DIR WORKSTATION_DIR DIST_DIR BUILD_DIR VERSION ARCH

WORKSTATION_SCRIPTS=(
  legacy/proxnix-workstation-common.sh
  bin/proxnix
  bin/proxnix-publish
  bin/proxnix-secrets
  bin/proxnix-doctor
  bin/proxnix-lxc-exercise
  bin/proxnix-tui
)

die() {
  echo "error: $*" >&2
  exit 1
}

prepare_dirs() {
  mkdir -p "$DIST_DIR" "$BUILD_DIR"
}

install_workstation_bin() {
  local dest="$1"
  mkdir -p "$dest"

  local script
  for script in "${WORKSTATION_SCRIPTS[@]}"; do
    install -m 755 "${WORKSTATION_DIR}/${script}" "${dest}/$(basename "${script}")"
  done
}

install_workstation_python_runtime() {
  local dest_root="$1"
  local runtime_dir="${dest_root}/lib/python"
  mkdir -p "$runtime_dir"
  cp -a "${WORKSTATION_DIR}/src/proxnix_workstation" "${runtime_dir}/proxnix_workstation"

  local python_bin="${WORKSTATION_DIR}/.venv/bin/python"
  if [[ ! -x "$python_bin" ]]; then
    python_bin="${WORKSTATION_DIR}/.venv/bin/python3"
  fi
  if [[ ! -x "$python_bin" ]]; then
    python_bin="${PYTHON:-python3}"
  fi

  local bundle_paths
  bundle_paths="$("$python_bin" - <<'PY'
from importlib.util import find_spec
from pathlib import Path

module_names = [
    "cryptography",
    "cffi",
    "pycparser",
    "_cffi_backend",
]

paths = []
for name in module_names:
    spec = find_spec(name)
    if spec is None:
        continue
    if spec.submodule_search_locations:
        for location in spec.submodule_search_locations:
            paths.append(Path(location))
    elif spec.origin:
        paths.append(Path(spec.origin))

for path in paths:
    print(path)
PY
)"

  if [[ -z "$bundle_paths" ]]; then
    die "no bundled Python dependencies found; run uv sync --project ${WORKSTATION_DIR} first"
  fi

  local dep_path
  while IFS= read -r dep_path; do
    [[ -n "$dep_path" ]] || continue
    if [[ -d "$dep_path" ]]; then
      cp -a "$dep_path" "${runtime_dir}/$(basename "$dep_path")"
    elif [[ -f "$dep_path" ]]; then
      install -m 755 "$dep_path" "${runtime_dir}/$(basename "$dep_path")"
    fi
  done <<< "$bundle_paths"
}

write_packaged_cli_wrappers() {
  local bin_dir="$1"
  local runtime_root="$2"
  local relative_python
  relative_python="$(python3 - <<'PY' "$bin_dir" "$runtime_root/lib/python"
from pathlib import Path
import os
import sys

bin_dir = Path(sys.argv[1])
python_dir = Path(sys.argv[2])
print(os.path.relpath(python_dir, bin_dir))
PY
)"

  cat > "${bin_dir}/proxnix-python" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ -x /opt/homebrew/opt/python@3.12/bin/python3.12 ]]; then
  exec /opt/homebrew/opt/python@3.12/bin/python3.12 "$@"
fi
if [[ -x /usr/local/opt/python@3.12/bin/python3.12 ]]; then
  exec /usr/local/opt/python@3.12/bin/python3.12 "$@"
fi
exec python3 "$@"
EOF

  cat > "${bin_dir}/proxnix" <<EOF
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
exec env PYTHONPATH="\$SCRIPT_DIR/${relative_python}" "\$SCRIPT_DIR/proxnix-python" -m proxnix_workstation.cli "\$@"
EOF

  cat > "${bin_dir}/proxnix-secrets" <<EOF
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
exec env PYTHONPATH="\$SCRIPT_DIR/${relative_python}" "\$SCRIPT_DIR/proxnix-python" -m proxnix_workstation.secrets_cli "\$@"
EOF

  cat > "${bin_dir}/proxnix-publish" <<EOF
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
exec env PYTHONPATH="\$SCRIPT_DIR/${relative_python}" "\$SCRIPT_DIR/proxnix-python" -m proxnix_workstation.publish_cli "\$@"
EOF

  cat > "${bin_dir}/proxnix-doctor" <<EOF
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
exec env PYTHONPATH="\$SCRIPT_DIR/${relative_python}" "\$SCRIPT_DIR/proxnix-python" -m proxnix_workstation.doctor_cli "\$@"
EOF

  cat > "${bin_dir}/proxnix-lxc-exercise" <<EOF
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
exec env PYTHONPATH="\$SCRIPT_DIR/${relative_python}" "\$SCRIPT_DIR/proxnix-python" -m proxnix_workstation.exercise_cli "\$@"
EOF

  cat > "${bin_dir}/proxnix-tui" <<EOF
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
exec env PYTHONPATH="\$SCRIPT_DIR/${relative_python}" "\$SCRIPT_DIR/proxnix-python" -m proxnix_workstation.tui "\$@"
EOF

  chmod 755 "${bin_dir}/proxnix-python" "${bin_dir}/proxnix" "${bin_dir}/proxnix-secrets" "${bin_dir}/proxnix-publish" "${bin_dir}/proxnix-doctor" "${bin_dir}/proxnix-lxc-exercise" "${bin_dir}/proxnix-tui"
}

write_runtime_readme() {
  local dest="$1" package_label="$2"

  cat > "${dest}/README.txt" <<EOF
proxnix workstation tools (${package_label})
==========================================

Included CLI tools:
  - proxnix
  - proxnix-publish
  - proxnix-secrets
  - proxnix-doctor
  - proxnix-lxc-exercise
  - proxnix-tui

Runtime dependencies:
  - bash
  - python 3.12
  - ssh
  - rsync
  - sops

Bundled Python dependencies:
  - cryptography

Configuration:
  ~/.config/proxnix/config
EOF
}
