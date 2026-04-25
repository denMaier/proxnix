from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SecretScopeRef:
    scope: str
    vmid: str | None = None
    group: str | None = None

    def cli_args(self) -> list[str]:
        args = ["--scope", self.scope]
        if self.vmid is not None:
            args.extend(["--vmid", self.vmid])
        if self.group is not None:
            args.extend(["--group", self.group])
        return args

    def label(self) -> str:
        if self.scope == "shared":
            return "shared"
        if self.scope == "group":
            return f"group:{self.group}"
        if self.scope == "container":
            return f"container:{self.vmid}"
        return self.scope


def shared_scope() -> SecretScopeRef:
    return SecretScopeRef(scope="shared")


def group_scope(group: str) -> SecretScopeRef:
    return SecretScopeRef(scope="group", group=group)


def container_scope(vmid: str) -> SecretScopeRef:
    return SecretScopeRef(scope="container", vmid=vmid)


class SecretProvider:
    name = "unknown"

    def supports(self, capability: str) -> bool:
        raise NotImplementedError

    def list_names(self, ref: SecretScopeRef) -> list[str]:
        raise NotImplementedError

    def get(self, ref: SecretScopeRef, name: str) -> str | None:
        raise NotImplementedError

    def set(self, ref: SecretScopeRef, name: str, value: str) -> None:
        raise NotImplementedError

    def remove(self, ref: SecretScopeRef, name: str) -> None:
        raise NotImplementedError

    def export_scope(self, ref: SecretScopeRef) -> dict[str, str]:
        raise NotImplementedError

    def has_any(self, ref: SecretScopeRef) -> bool:
        return bool(self.export_scope(ref))

    def describe(self) -> str:
        return self.name
