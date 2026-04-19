from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from .errors import PlanningError
from .planning import ChangeKind, PlannedChange, Resource


def _mode_bits(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class _TreeSnapshot:
    root_mode: int
    directories: dict[Path, int]
    files: dict[Path, tuple[str, int]]


def _snapshot_tree(root: Path) -> _TreeSnapshot:
    if root.exists() and not root.is_dir():
        raise PlanningError(f"expected directory tree but found non-directory: {root}")

    directories: dict[Path, int] = {}
    files: dict[Path, tuple[str, int]] = {}

    if not root.exists():
        return _TreeSnapshot(root_mode=0o755, directories=directories, files=files)

    for path in sorted(root.rglob("*")):
        rel_path = path.relative_to(root)
        if path.is_symlink():
            raise PlanningError(f"symlinks are not supported in managed trees: {path}")
        if path.is_dir():
            directories[rel_path] = _mode_bits(path)
        elif path.is_file():
            files[rel_path] = (_sha256_file(path), _mode_bits(path))
        else:
            raise PlanningError(f"unsupported filesystem entry in managed tree: {path}")

    return _TreeSnapshot(
        root_mode=_mode_bits(root),
        directories=directories,
        files=files,
    )


class MirrorTree(Resource):
    """Make a destination tree match a desired source tree exactly."""

    def __init__(self, source_root: Path, destination_root: Path, *, name: str | None = None) -> None:
        super().__init__(name or f"mirror-tree:{destination_root}")
        self.source_root = source_root
        self.destination_root = destination_root

    def plan_changes(self) -> list[PlannedChange]:
        if not self.source_root.is_dir():
            raise PlanningError(f"source tree not found: {self.source_root}")
        if self.destination_root.exists() and not self.destination_root.is_dir():
            raise PlanningError(
                f"destination exists as a non-directory: {self.destination_root}"
            )

        source = _snapshot_tree(self.source_root)
        destination = _snapshot_tree(self.destination_root)
        changes: list[PlannedChange] = []

        if not self.destination_root.exists():
            changes.append(
                PlannedChange(
                    kind=ChangeKind.CREATE,
                    path=self.destination_root,
                    details="create destination root",
                )
            )
        elif destination.root_mode != source.root_mode:
            changes.append(
                PlannedChange(
                    kind=ChangeKind.UPDATE,
                    path=self.destination_root,
                    details=f"mode {oct(destination.root_mode)} -> {oct(source.root_mode)}",
                )
            )

        for rel_path, source_mode in sorted(source.directories.items()):
            dest_path = self.destination_root / rel_path
            if rel_path not in destination.directories:
                if rel_path in destination.files:
                    raise PlanningError(f"destination path is a file, expected directory: {dest_path}")
                changes.append(
                    PlannedChange(
                        kind=ChangeKind.CREATE,
                        path=dest_path,
                        details="create directory",
                    )
                )
                continue
            dest_mode = destination.directories[rel_path]
            if dest_mode != source_mode:
                changes.append(
                    PlannedChange(
                        kind=ChangeKind.UPDATE,
                        path=dest_path,
                        details=f"mode {oct(dest_mode)} -> {oct(source_mode)}",
                    )
                )

        for rel_path, (source_hash, source_mode) in sorted(source.files.items()):
            dest_path = self.destination_root / rel_path
            if rel_path not in destination.files:
                if rel_path in destination.directories:
                    raise PlanningError(f"destination path is a directory, expected file: {dest_path}")
                changes.append(
                    PlannedChange(
                        kind=ChangeKind.CREATE,
                        path=dest_path,
                        details="create file",
                    )
                )
                continue
            dest_hash, dest_mode = destination.files[rel_path]
            if dest_hash != source_hash or dest_mode != source_mode:
                details: list[str] = []
                if dest_hash != source_hash:
                    details.append("content")
                if dest_mode != source_mode:
                    details.append(f"mode {oct(dest_mode)} -> {oct(source_mode)}")
                changes.append(
                    PlannedChange(
                        kind=ChangeKind.UPDATE,
                        path=dest_path,
                        details=", ".join(details),
                    )
                )

        extra_files = set(destination.files) - set(source.files)
        extra_dirs = set(destination.directories) - set(source.directories)

        for rel_path in sorted(extra_files):
            changes.append(
                PlannedChange(
                    kind=ChangeKind.DELETE,
                    path=self.destination_root / rel_path,
                    details="remove extra file",
                )
            )

        for rel_path in sorted(extra_dirs, key=lambda value: len(value.parts), reverse=True):
            changes.append(
                PlannedChange(
                    kind=ChangeKind.DELETE,
                    path=self.destination_root / rel_path,
                    details="remove extra directory",
                )
            )

        return changes

    def apply_changes(self, changes: list[PlannedChange]) -> None:
        self.destination_root.mkdir(parents=True, exist_ok=True)
        shutil.copystat(self.source_root, self.destination_root, follow_symlinks=False)

        source = _snapshot_tree(self.source_root)

        for rel_path in sorted(source.directories, key=lambda value: len(value.parts)):
            src_path = self.source_root / rel_path
            dest_path = self.destination_root / rel_path
            dest_path.mkdir(parents=True, exist_ok=True)
            shutil.copystat(src_path, dest_path, follow_symlinks=False)

        for rel_path in sorted(source.files):
            src_path = self.source_root / rel_path
            dest_path = self.destination_root / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path, follow_symlinks=False)
            dest_path.chmod(_mode_bits(src_path))

        for change in sorted(
            (change for change in changes if change.kind == ChangeKind.DELETE),
            key=lambda item: len(item.path.relative_to(self.destination_root).parts),
            reverse=True,
        ):
            path = change.path
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
