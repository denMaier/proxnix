from __future__ import annotations

import re
import shutil
from pathlib import Path

from .config import WorkstationConfig
from .errors import PlanningError
from .paths import SitePaths


_VMID_RE = re.compile(r"^[0-9]+$")


def _copy_tree_if_present(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    shutil.copytree(source, destination, dirs_exist_ok=True)


def _copy_file_if_present(source: Path, destination: Path) -> None:
    if not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def build_desired_config_tree(
    config: WorkstationConfig,
    destination_root: Path,
    *,
    target_vmid: str | None = None,
) -> Path:
    site_paths = SitePaths.from_config(config)
    destination_root.mkdir(parents=True, exist_ok=True)

    containers_destination = destination_root / "containers"
    containers_destination.mkdir(parents=True, exist_ok=True)

    if target_vmid is not None and not _VMID_RE.match(target_vmid):
        raise PlanningError(f"container VMID must be numeric: {target_vmid}")

    if target_vmid is None:
        _copy_file_if_present(site_paths.site_nix, destination_root / "site.nix")
        _copy_tree_if_present(site_paths.containers_dir, containers_destination)
        return destination_root

    _copy_tree_if_present(
        site_paths.containers_dir / "_template",
        containers_destination / "_template",
    )
    _copy_tree_if_present(
        site_paths.container_dir(target_vmid),
        containers_destination / target_vmid,
    )
    return destination_root
