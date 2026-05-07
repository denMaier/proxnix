from __future__ import annotations

import re
from pathlib import Path

from .config import WorkstationConfig
from .errors import PlanningError
from .paths import SitePaths
from .publish_cli import PublishOptions, build_publish_tree


_VMID_RE = re.compile(r"^[0-9]+$")


def build_desired_config_tree(
    config: WorkstationConfig,
    destination_root: Path,
    *,
    target_vmid: str | None = None,
) -> Path:
    site_paths = SitePaths.from_config(config)
    destination_root.mkdir(parents=True, exist_ok=True)

    if target_vmid is not None and not _VMID_RE.match(target_vmid):
        raise PlanningError(f"container VMID must be numeric: {target_vmid}")

    build_publish_tree(
        config,
        site_paths,
        PublishOptions(config_only=True, target_vmid=target_vmid),
        destination_root,
    )
    return destination_root
