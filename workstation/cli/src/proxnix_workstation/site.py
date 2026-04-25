from __future__ import annotations

import re
from pathlib import Path

from .errors import PlanningError
from .paths import SitePaths


_SECRET_GROUP_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):")


def valid_secret_group_name(group: str) -> bool:
    return bool(group) and _SECRET_GROUP_RE.fullmatch(group) is not None


def read_container_secret_groups(site_paths: SitePaths, vmid: str) -> list[str]:
    path = site_paths.container_secret_groups_file(vmid)
    if not path.is_file():
        return []

    groups: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if not valid_secret_group_name(line):
            raise PlanningError(f"invalid secret group name in {path}: {line}")
        if line not in seen:
            seen.add(line)
            groups.append(line)
    return groups


def collect_site_vmids(site_paths: SitePaths) -> list[str]:
    vmids: set[str] = set()
    for base in (site_paths.containers_dir, site_paths.private_dir / "containers"):
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if entry.is_dir() and entry.name.isdigit():
                vmids.add(entry.name)
    return sorted(vmids, key=int)



def top_level_keys(path: Path) -> list[str]:
    if not path.is_file():
        return []

    keys: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _TOP_LEVEL_KEY_RE.match(raw_line)
        if match is None:
            continue
        key = match.group(1)
        if key != "sops":
            keys.add(key)
    return sorted(keys)
