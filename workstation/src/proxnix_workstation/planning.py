from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .errors import PlanningError


class ChangeKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class ResourceStatus(StrEnum):
    OK = "ok"
    CHANGED = "changed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PlannedChange:
    kind: ChangeKind
    path: Path
    details: str = ""


@dataclass
class ResourceResult:
    resource: str
    status: ResourceStatus
    message: str
    changes: list[PlannedChange] = field(default_factory=list)


@dataclass
class ExecutionReport:
    check: bool
    results: list[ResourceResult]

    @property
    def has_failures(self) -> bool:
        return any(result.status == ResourceStatus.FAILED for result in self.results)

    @property
    def has_changes(self) -> bool:
        return any(result.status == ResourceStatus.CHANGED for result in self.results)

    def render_text(self) -> str:
        lines: list[str] = []
        mode = "check" if self.check else "apply"
        lines.append(f"[{mode}]")
        for result in self.results:
            lines.append(f"  {result.status.upper():7} {result.resource}: {result.message}")
            for change in result.changes:
                suffix = f" ({change.details})" if change.details else ""
                lines.append(f"           {change.kind} {change.path}{suffix}")
        return "\n".join(lines)


class Resource(ABC):
    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def plan_changes(self) -> list[PlannedChange]:
        """Return the desired mutations for this resource."""

    @abstractmethod
    def apply_changes(self, changes: list[PlannedChange]) -> None:
        """Apply a previously computed list of changes."""

    def run(self, *, check: bool) -> ResourceResult:
        try:
            changes = self.plan_changes()
            if not changes:
                return ResourceResult(
                    resource=self.name,
                    status=ResourceStatus.OK,
                    message="already converged",
                )
            if check:
                return ResourceResult(
                    resource=self.name,
                    status=ResourceStatus.CHANGED,
                    message=f"{len(changes)} change(s) planned",
                    changes=changes,
                )
            self.apply_changes(changes)
            return ResourceResult(
                resource=self.name,
                status=ResourceStatus.CHANGED,
                message=f"{len(changes)} change(s) applied",
                changes=changes,
            )
        except (OSError, PlanningError) as exc:
            return ResourceResult(
                resource=self.name,
                status=ResourceStatus.FAILED,
                message=str(exc),
            )


class PlanRunner:
    def __init__(self, resources: list[Resource]) -> None:
        self.resources = resources

    def run(self, *, check: bool) -> ExecutionReport:
        return ExecutionReport(
            check=check,
            results=[resource.run(check=check) for resource in self.resources],
        )
