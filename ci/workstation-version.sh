#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYPROJECT="${ROOT_DIR}/workstation/pyproject.toml"
VERSION_FILE="${ROOT_DIR}/VERSION"

python3 - <<'PY' "$PYPROJECT" "$VERSION_FILE"
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
version_file = Path(sys.argv[2])
text = path.read_text(encoding="utf-8")
match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', text)
if not match:
    raise SystemExit("error: could not find [project].version in workstation/pyproject.toml")
pyproject_version = match.group(1)
version_text = version_file.read_text(encoding="utf-8").strip()
if pyproject_version != version_text:
    raise SystemExit(
        f"error: VERSION ({version_text}) does not match workstation/pyproject.toml ({pyproject_version})"
    )
print(pyproject_version)
PY
