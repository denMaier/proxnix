from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
from pathlib import Path

from .errors import ProxnixWorkstationError
from .runtime import command_env, run_command


_TREE_GLYPH_PREFIX = re.compile(r"^[\s│├└─┬┼╰╭━]+")


class AdapterError(ProxnixWorkstationError):
    pass


def _json_ok(**payload: object) -> int:
    print(json.dumps({"ok": True, **payload}))
    return 0


def _json_error(message: str) -> int:
    print(json.dumps({"ok": False, "error": message}))
    return 1


def _scope_prefix(scope: str, *, vmid: str | None, group: str | None, root: str) -> str:
    if scope == "shared":
        return f"{root}/shared"
    if scope == "group":
        if not group:
            raise AdapterError("group scope requires --group")
        return f"{root}/groups/{group}"
    if scope == "container":
        if not vmid:
            raise AdapterError("container scope requires --vmid")
        return f"{root}/containers/{vmid}"
    raise AdapterError(f"unsupported scope: {scope}")


def _normalize_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = _TREE_GLYPH_PREFIX.sub("", raw_line.strip())
        line = line.strip()
        if line:
            lines.append(line)
    return lines


def _env_path(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        return ""
    return str(Path(value).expanduser())


class _BaseNamedAdapter:
    name = "unknown"

    def capabilities(self) -> list[str]:
        return ["list", "get", "set", "remove", "export-scope"]

    def root_prefix(self) -> str:
        return os.environ.get("PROXNIX_SECRET_PATH_PREFIX", "proxnix").strip("/") or "proxnix"

    def scope_path(self, *, scope: str, vmid: str | None, group: str | None) -> str:
        return _scope_prefix(scope, vmid=vmid, group=group, root=self.root_prefix())

    def secret_path(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str:
        if not name:
            raise AdapterError("secret name must not be empty")
        return f"{self.scope_path(scope=scope, vmid=vmid, group=group)}/{name}"

    def list(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        raise NotImplementedError

    def get(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str | None:
        raise NotImplementedError

    def set(self, *, scope: str, vmid: str | None, group: str | None, name: str, value: str) -> None:
        raise NotImplementedError

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        raise NotImplementedError

    def export_scope(self, *, scope: str, vmid: str | None, group: str | None) -> dict[str, str]:
        data: dict[str, str] = {}
        for name in self.list(scope=scope, vmid=vmid, group=group):
            value = self.get(scope=scope, vmid=vmid, group=group, name=name)
            if value is not None:
                data[name] = value
        return data

class _PasswordStoreAdapter(_BaseNamedAdapter):
    command_name = ""
    store_dir_env_name = ""
    store_dir_fallback = ""

    def _command_env(self) -> dict[str, str]:
        env = command_env()
        store_dir = _env_path(self.store_dir_env_name)
        if store_dir:
            env["PASSWORD_STORE_DIR"] = store_dir
        elif self.store_dir_fallback and "PASSWORD_STORE_DIR" not in env:
            env["PASSWORD_STORE_DIR"] = str(Path(self.store_dir_fallback).expanduser())
        return env

    def _run(self, args: list[str], *, input_text: str | None = None, check: bool = True):
        return run_command(
            [self.command_name, *args],
            env=self._command_env(),
            input_text=input_text,
            check=check,
        )

    def list(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        target = self.scope_path(scope=scope, vmid=vmid, group=group)
        completed = self._run(["ls", target], check=False)
        if completed.returncode != 0:
            return []
        scope_leaf = target.rsplit("/", 1)[-1]
        names: list[str] = []
        for line in _normalize_lines(completed.stdout):
            if line in {target, scope_leaf, "Password Store"}:
                continue
            names.append(line.rsplit("/", 1)[-1])
        return sorted(set(names))

    def get(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str | None:
        target = self.secret_path(scope=scope, vmid=vmid, group=group, name=name)
        completed = self._run(["show", target], check=False)
        if completed.returncode != 0:
            return None
        return completed.stdout.rstrip("\n")

    def set(self, *, scope: str, vmid: str | None, group: str | None, name: str, value: str) -> None:
        target = self.secret_path(scope=scope, vmid=vmid, group=group, name=name)
        self._run(["insert", "-m", "-f", target], input_text=value)

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        target = self.secret_path(scope=scope, vmid=vmid, group=group, name=name)
        self._run(["rm", "-f", target], check=False)

class PassAdapter(_PasswordStoreAdapter):
    name = "pass"
    command_name = "pass"
    store_dir_env_name = "PROXNIX_PASS_STORE_DIR"
    store_dir_fallback = "~/.password-store"


class GoPassAdapter(_PasswordStoreAdapter):
    name = "gopass"
    command_name = "gopass"
    store_dir_env_name = "PROXNIX_GOPASS_STORE_DIR"
    store_dir_fallback = "~/.local/share/gopass/stores/root"


class PassholeAdapter(_BaseNamedAdapter):
    name = "passhole"

    def _database_args(self) -> list[str]:
        args: list[str] = []
        database = _env_path("PROXNIX_PASSHOLE_DATABASE")
        config = _env_path("PROXNIX_PASSHOLE_CONFIG")
        if database:
            args.extend(["--database", database])
        elif config:
            args.extend(["--config", config])
        else:
            raise AdapterError(
                "PROXNIX_PASSHOLE_DATABASE or PROXNIX_PASSHOLE_CONFIG is required for passhole provider"
            )
        keyfile = _env_path("PROXNIX_PASSHOLE_KEYFILE")
        if keyfile:
            args.extend(["--keyfile", keyfile])
        no_password = os.environ.get("PROXNIX_PASSHOLE_NO_PASSWORD", "").strip().lower()
        if no_password in {"1", "true", "yes"}:
            args.append("--no-password")
        elif self._password_text() is not None:
            args.extend(["--password", "-"])
        no_cache = os.environ.get("PROXNIX_PASSHOLE_NO_CACHE", "").strip().lower()
        if no_cache in {"1", "true", "yes"}:
            args.append("--no-cache")
        cache_timeout = os.environ.get("PROXNIX_PASSHOLE_CACHE_TIMEOUT", "").strip()
        if cache_timeout:
            args.extend(["--cache-timeout", cache_timeout])
        return args

    def _password_text(self) -> str | None:
        password = os.environ.get("PROXNIX_PASSHOLE_PASSWORD", "")
        if password:
            return password if password.endswith("\n") else password + "\n"
        password_file = _env_path("PROXNIX_PASSHOLE_PASSWORD_FILE")
        if not password_file:
            return None
        value = Path(password_file).expanduser().read_text(encoding="utf-8")
        return value if value.endswith("\n") else value + "\n"

    def _run(self, args: list[str], *, check: bool = True):
        return run_command(
            ["ph", *self._database_args(), *args],
            input_text=self._password_text(),
            check=check,
        )

    def _scope_components(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        return self.scope_path(scope=scope, vmid=vmid, group=group).split("/")

    def _entry_components(
        self,
        *,
        scope: str,
        vmid: str | None,
        group: str | None,
        name: str,
    ) -> list[str]:
        return self.secret_path(scope=scope, vmid=vmid, group=group, name=name).split("/")

    def _eval_json(self, expr: str, *, check: bool = True) -> object:
        completed = self._run(["eval", "--json", expr], check=check)
        if completed.returncode != 0:
            return None
        return json.loads(completed.stdout or "null")

    def list(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        payload = self._eval_json(
            "(lambda group: sorted([entry.title for entry in (group.entries if group else []) if entry.title]))"
            f"(kp.find_groups(path={json.dumps(self._scope_components(scope=scope, vmid=vmid, group=group))}, first=True))",
            check=False,
        )
        if not isinstance(payload, list):
            return []
        return sorted({name for name in payload if isinstance(name, str)})

    def get(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str | None:
        payload = self._eval_json(
            "(lambda entry: entry.password if entry else None)"
            f"(kp.find_entries(path={json.dumps(self._entry_components(scope=scope, vmid=vmid, group=group, name=name))}, first=True))",
            check=False,
        )
        return payload if isinstance(payload, str) else None

    def set(self, *, scope: str, vmid: str | None, group: str | None, name: str, value: str) -> None:
        scope_components = json.dumps(self._scope_components(scope=scope, vmid=vmid, group=group))
        entry_components = json.dumps(
            self._entry_components(scope=scope, vmid=vmid, group=group, name=name)
        )
        code = "\n".join(
            [
                f"scope_path = {scope_components}",
                f"entry_path = {entry_components}",
                "group = kp.root_group",
                "current_path = []",
                "for segment in scope_path:",
                "    current_path.append(segment)",
                "    next_group = kp.find_groups(path=current_path, first=True)",
                "    if next_group is None:",
                "        next_group = kp.add_group(group, segment)",
                "    group = next_group",
                "entry = kp.find_entries(path=entry_path, first=True)",
                "if entry is None:",
                f"    kp.add_entry(group, {json.dumps(name)}, '', {json.dumps(value)}, url='', otp='')",
                "else:",
                f"    entry._set_string_field('Password', {json.dumps(value)})",
                "kp.save()",
            ]
        )
        self._run(["eval", code])

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        code = "\n".join(
            [
                f"entry = kp.find_entries(path={json.dumps(self._entry_components(scope=scope, vmid=vmid, group=group, name=name))}, first=True)",
                "if entry is not None:",
                "    entry.delete()",
                "    kp.save()",
            ]
        )
        self._run(["eval", code], check=False)

    def export_scope(self, *, scope: str, vmid: str | None, group: str | None) -> dict[str, str]:
        payload = self._eval_json(
            "(lambda group: {entry.title: entry.password for entry in (group.entries if group else []) "
            "if entry.title and entry.password is not None})"
            f"(kp.find_groups(path={json.dumps(self._scope_components(scope=scope, vmid=vmid, group=group))}, first=True))",
            check=False,
        )
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, str)}

class PyKeePassAdapter(_BaseNamedAdapter):
    name = "pykeepass"

    def _database_path(self) -> str:
        path = _env_path("PROXNIX_PYKEEPASS_DATABASE")
        if not path:
            raise AdapterError("PROXNIX_PYKEEPASS_DATABASE is required for pykeepass provider")
        return path

    def _keyfile_path(self) -> str | None:
        path = _env_path("PROXNIX_PYKEEPASS_KEYFILE")
        return path or None

    def _password(self) -> str | None:
        no_password = os.environ.get("PROXNIX_PYKEEPASS_NO_PASSWORD", "").strip().lower()
        if no_password in {"1", "true", "yes"}:
            return None
        password = os.environ.get("PROXNIX_PYKEEPASS_PASSWORD", "")
        if password:
            return password
        password_file = _env_path("PROXNIX_PYKEEPASS_PASSWORD_FILE")
        if not password_file:
            return None
        return Path(password_file).expanduser().read_text(encoding="utf-8").rstrip("\n")

    def _pykeepass_class(self):
        try:
            module = importlib.import_module("pykeepass")
        except ImportError as exc:
            raise AdapterError("pykeepass is not installed") from exc
        pykeepass_class = getattr(module, "PyKeePass", None)
        if pykeepass_class is None:
            raise AdapterError("pykeepass module does not expose PyKeePass")
        return pykeepass_class

    def _open_database(self):
        pykeepass_class = self._pykeepass_class()
        try:
            return pykeepass_class(
                self._database_path(),
                password=self._password(),
                keyfile=self._keyfile_path(),
            )
        except Exception as exc:
            raise AdapterError(f"failed to open pykeepass database: {exc}") from exc

    def _scope_components(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        return self.scope_path(scope=scope, vmid=vmid, group=group).split("/")

    def _find_group(self, kp, components: list[str], *, create: bool):
        target = kp.root_group
        for segment in components:
            next_group = next((item for item in target.subgroups if item.name == segment), None)
            if next_group is None:
                if not create:
                    return None
                next_group = kp.add_group(target, segment)
            target = next_group
        return target

    def _find_entry(self, group, name: str):
        return next((entry for entry in group.entries if entry.title == name), None)

    def list(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        kp = self._open_database()
        target_group = self._find_group(
            kp,
            self._scope_components(scope=scope, vmid=vmid, group=group),
            create=False,
        )
        if target_group is None:
            return []
        names = [entry.title for entry in target_group.entries if isinstance(entry.title, str) and entry.title]
        return sorted(set(names))

    def get(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str | None:
        kp = self._open_database()
        target_group = self._find_group(
            kp,
            self._scope_components(scope=scope, vmid=vmid, group=group),
            create=False,
        )
        if target_group is None:
            return None
        entry = self._find_entry(target_group, name)
        if entry is None:
            return None
        value = getattr(entry, "password", None)
        return value if isinstance(value, str) else None

    def set(self, *, scope: str, vmid: str | None, group: str | None, name: str, value: str) -> None:
        kp = self._open_database()
        target_group = self._find_group(
            kp,
            self._scope_components(scope=scope, vmid=vmid, group=group),
            create=True,
        )
        entry = self._find_entry(target_group, name)
        if entry is None:
            kp.add_entry(target_group, name, "", value, url="")
        else:
            entry.password = value
        kp.save()

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        kp = self._open_database()
        target_group = self._find_group(
            kp,
            self._scope_components(scope=scope, vmid=vmid, group=group),
            create=False,
        )
        if target_group is None:
            return
        entry = self._find_entry(target_group, name)
        if entry is None:
            return
        kp.delete_entry(entry)
        kp.save()

    def export_scope(self, *, scope: str, vmid: str | None, group: str | None) -> dict[str, str]:
        kp = self._open_database()
        target_group = self._find_group(
            kp,
            self._scope_components(scope=scope, vmid=vmid, group=group),
            create=False,
        )
        if target_group is None:
            return {}
        data: dict[str, str] = {}
        for entry in target_group.entries:
            if isinstance(entry.title, str) and entry.title and isinstance(entry.password, str):
                data[entry.title] = entry.password
        return data

class KeePassXCCliAdapter(_BaseNamedAdapter):
    name = "keepassxc-cli"

    def capabilities(self) -> list[str]:
        return ["list", "get", "set", "remove", "export-scope"]

    def _database_path(self) -> str:
        path = _env_path("PROXNIX_KEEPASSXC_DATABASE")
        if not path:
            raise AdapterError("PROXNIX_KEEPASSXC_DATABASE is required for keepassxc-cli provider")
        return path

    def _unlock_args(self) -> list[str]:
        args: list[str] = []
        password_file = _env_path("PROXNIX_KEEPASSXC_PASSWORD_FILE")
        key_file = _env_path("PROXNIX_KEEPASSXC_KEY_FILE")
        no_password = os.environ.get("PROXNIX_KEEPASSXC_NO_PASSWORD", "").strip().lower()
        if password_file:
            args.extend(["--password-file", password_file])
        if key_file:
            args.extend(["--key-file", key_file])
        if no_password in {"1", "true", "yes"}:
            args.append("--no-password")
        return args

    def _run(self, args: list[str], *, input_text: str | None = None, check: bool = True):
        return run_command(["keepassxc-cli", *args], input_text=input_text, check=check)

    def list(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        target = self.scope_path(scope=scope, vmid=vmid, group=group)
        completed = self._run(
            ["ls", *self._unlock_args(), self._database_path(), target],
            check=False,
        )
        if completed.returncode != 0:
            return []
        scope_leaf = target.rsplit("/", 1)[-1]
        names: list[str] = []
        for line in _normalize_lines(completed.stdout):
            if line in {target, scope_leaf}:
                continue
            names.append(line.rsplit("/", 1)[-1])
        return sorted(set(names))

    def get(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str | None:
        target = self.secret_path(scope=scope, vmid=vmid, group=group, name=name)
        completed = self._run(
            ["show", "-q", "-s", "-a", "password", *self._unlock_args(), self._database_path(), target],
            check=False,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.rstrip("\n")

    def set(self, *, scope: str, vmid: str | None, group: str | None, name: str, value: str) -> None:
        target = self.secret_path(scope=scope, vmid=vmid, group=group, name=name)
        self.remove(scope=scope, vmid=vmid, group=group, name=name)
        self._run(
            ["add", "-q", "-p", *self._unlock_args(), self._database_path(), target],
            input_text=value,
        )

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        target = self.secret_path(scope=scope, vmid=vmid, group=group, name=name)
        self._run(
            ["rm", "-q", *self._unlock_args(), self._database_path(), target],
            check=False,
        )

class OnePasswordAdapter(_BaseNamedAdapter):
    name = "op"

    def _vault(self) -> str:
        vault = os.environ.get("PROXNIX_1PASSWORD_VAULT", "").strip()
        if not vault:
            raise AdapterError("PROXNIX_1PASSWORD_VAULT is required for 1Password provider")
        return vault

    def _account_args(self) -> list[str]:
        account = os.environ.get("PROXNIX_1PASSWORD_ACCOUNT", "").strip()
        if not account:
            return []
        return ["--account", account]

    def _run(self, args: list[str], *, input_text: str | None = None, check: bool = True):
        return run_command(["op", *args], input_text=input_text, check=check)

    def _scope_tag(self, *, scope: str, vmid: str | None, group: str | None) -> str:
        return self.scope_path(scope=scope, vmid=vmid, group=group)

    def _field_value(self, payload: dict[str, object]) -> str | None:
        fields = payload.get("fields")
        if not isinstance(fields, list):
            return None
        for item in fields:
            if not isinstance(item, dict):
                continue
            field_id = item.get("id")
            label = item.get("label")
            purpose = item.get("purpose")
            if field_id == "password" or purpose == "PASSWORD":
                value = item.get("value")
                if isinstance(value, str):
                    return value
            if isinstance(label, str) and label.lower() == "password":
                value = item.get("value")
                if isinstance(value, str):
                    return value
        return None

    def _list_items(self, *, scope: str, vmid: str | None, group: str | None) -> list[dict[str, object]]:
        completed = self._run(
            [
                "item",
                "list",
                "--vault",
                self._vault(),
                "--tags",
                self._scope_tag(scope=scope, vmid=vmid, group=group),
                "--format",
                "json",
                *self._account_args(),
            ],
            check=False,
        )
        if completed.returncode != 0:
            return []
        payload = json.loads(completed.stdout or "[]")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def list(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        names = [
            item["title"]
            for item in self._list_items(scope=scope, vmid=vmid, group=group)
            if isinstance(item.get("title"), str)
        ]
        return sorted(set(names))

    def get(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str | None:
        for item in self._list_items(scope=scope, vmid=vmid, group=group):
            if item.get("title") != name or not isinstance(item.get("id"), str):
                continue
            completed = self._run(
                [
                    "item",
                    "get",
                    item["id"],
                    "--vault",
                    self._vault(),
                    "--format",
                    "json",
                    *self._account_args(),
                ],
                check=False,
            )
            if completed.returncode != 0:
                return None
            payload = json.loads(completed.stdout or "{}")
            if isinstance(payload, dict):
                return self._field_value(payload)
        return None

    def set(self, *, scope: str, vmid: str | None, group: str | None, name: str, value: str) -> None:
        scope_tag = self._scope_tag(scope=scope, vmid=vmid, group=group)
        for item in self._list_items(scope=scope, vmid=vmid, group=group):
            if item.get("title") == name and isinstance(item.get("id"), str):
                self._run(
                    [
                        "item",
                        "edit",
                        item["id"],
                        "--vault",
                        self._vault(),
                        "--title",
                        name,
                        "--tags",
                        scope_tag,
                        f"password={value}",
                        *self._account_args(),
                    ]
                )
                return
        self._run(
            [
                "item",
                "create",
                "--category",
                "Password",
                "--title",
                name,
                "--vault",
                self._vault(),
                "--tags",
                scope_tag,
                f"password={value}",
                *self._account_args(),
            ]
        )

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        for item in self._list_items(scope=scope, vmid=vmid, group=group):
            if item.get("title") == name and isinstance(item.get("id"), str):
                self._run(
                    [
                        "item",
                        "delete",
                        item["id"],
                        "--vault",
                        self._vault(),
                        *self._account_args(),
                    ],
                    check=False,
                )
                return

class BitwardenSecretsAdapter(_BaseNamedAdapter):
    name = "bws"

    def _run(self, args: list[str], *, input_text: str | None = None, check: bool = True):
        return run_command(["bws", *args], input_text=input_text, check=check)

    def _project_name(self, *, scope: str, vmid: str | None, group: str | None) -> str:
        return self.scope_path(scope=scope, vmid=vmid, group=group)

    def _projects(self) -> list[dict[str, object]]:
        completed = self._run(["project", "list"], check=False)
        if completed.returncode != 0:
            return []
        payload = json.loads(completed.stdout or "[]")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _project_id(self, *, scope: str, vmid: str | None, group: str | None) -> str | None:
        expected = self._project_name(scope=scope, vmid=vmid, group=group)
        for item in self._projects():
            if item.get("name") == expected and isinstance(item.get("id"), str):
                return item["id"]
        return None

    def _ensure_project_id(self, *, scope: str, vmid: str | None, group: str | None) -> str:
        project_id = self._project_id(scope=scope, vmid=vmid, group=group)
        if project_id is not None:
            return project_id
        completed = self._run(["project", "create", self._project_name(scope=scope, vmid=vmid, group=group)])
        payload = json.loads(completed.stdout or "{}")
        project_id = payload.get("id")
        if not isinstance(project_id, str) or not project_id:
            raise AdapterError("bws project create did not return a project id")
        return project_id

    def _list_secrets(self, *, project_id: str) -> list[dict[str, object]]:
        completed = self._run(["secret", "list", project_id], check=False)
        if completed.returncode != 0:
            return []
        payload = json.loads(completed.stdout or "[]")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def list(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        project_id = self._project_id(scope=scope, vmid=vmid, group=group)
        if project_id is None:
            return []
        names = [
            item["key"]
            for item in self._list_secrets(project_id=project_id)
            if isinstance(item.get("key"), str)
        ]
        return sorted(set(names))

    def get(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str | None:
        project_id = self._project_id(scope=scope, vmid=vmid, group=group)
        if project_id is None:
            return None
        for item in self._list_secrets(project_id=project_id):
            if item.get("key") == name and isinstance(item.get("value"), str):
                return item["value"]
        return None

    def set(self, *, scope: str, vmid: str | None, group: str | None, name: str, value: str) -> None:
        project_id = self._ensure_project_id(scope=scope, vmid=vmid, group=group)
        for item in self._list_secrets(project_id=project_id):
            if item.get("key") == name and isinstance(item.get("id"), str):
                self._run(
                    [
                        "secret",
                        "edit",
                        item["id"],
                        "--key",
                        name,
                        "--value",
                        value,
                        "--project-id",
                        project_id,
                    ]
                )
                return
        self._run(["secret", "create", name, value, project_id])

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        project_id = self._project_id(scope=scope, vmid=vmid, group=group)
        if project_id is None:
            return
        for item in self._list_secrets(project_id=project_id):
            if item.get("key") == name and isinstance(item.get("id"), str):
                self._run(["secret", "delete", item["id"]], check=False)
                return

    def export_scope(self, *, scope: str, vmid: str | None, group: str | None) -> dict[str, str]:
        project_id = self._project_id(scope=scope, vmid=vmid, group=group)
        if project_id is None:
            return {}
        data: dict[str, str] = {}
        for item in self._list_secrets(project_id=project_id):
            key = item.get("key")
            value = item.get("value")
            if isinstance(key, str) and isinstance(value, str):
                data[key] = value
        return data

class InfisicalAdapter(_BaseNamedAdapter):
    name = "infisical"

    def project_id(self) -> str:
        project_id = os.environ.get("PROXNIX_INFISICAL_PROJECT_ID", "").strip()
        if not project_id:
            raise AdapterError("PROXNIX_INFISICAL_PROJECT_ID is required for Infisical provider")
        return project_id

    def environment(self) -> str:
        return os.environ.get("PROXNIX_INFISICAL_ENV", "dev").strip() or "dev"

    def secret_type(self) -> str:
        return os.environ.get("PROXNIX_INFISICAL_TYPE", "shared").strip() or "shared"

    def path_prefix(self) -> str:
        return "/" + self.root_prefix().strip("/")

    def scope_path(self, *, scope: str, vmid: str | None, group: str | None) -> str:
        return "/" + _scope_prefix(scope, vmid=vmid, group=group, root=self.path_prefix().strip("/"))

    def _base_args(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        return [
            "--projectId",
            self.project_id(),
            "--env",
            self.environment(),
            "--path",
            self.scope_path(scope=scope, vmid=vmid, group=group),
        ]

    def _run(self, args: list[str], *, input_text: str | None = None, check: bool = True):
        return run_command(["infisical", *args], input_text=input_text, check=check)

    def export_scope(self, *, scope: str, vmid: str | None, group: str | None) -> dict[str, str]:
        completed = self._run(
            ["export", "--format=json", *self._base_args(scope=scope, vmid=vmid, group=group)],
            check=False,
        )
        if completed.returncode != 0:
            return {}
        payload = json.loads(completed.stdout or "{}")
        if not isinstance(payload, dict):
            raise AdapterError("infisical export did not return a JSON object")
        return {str(key): value for key, value in payload.items() if isinstance(value, str)}

    def list(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        return sorted(self.export_scope(scope=scope, vmid=vmid, group=group))

    def get(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str | None:
        completed = self._run(
            [
                "secrets",
                "get",
                name,
                "--plain",
                "--silent",
                *self._base_args(scope=scope, vmid=vmid, group=group),
            ],
            check=False,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.rstrip("\n")

    def set(self, *, scope: str, vmid: str | None, group: str | None, name: str, value: str) -> None:
        self._run(
            [
                "secrets",
                "set",
                f"{name}={value}",
                "--type",
                self.secret_type(),
                *self._base_args(scope=scope, vmid=vmid, group=group),
            ]
        )

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        self._run(
            [
                "secrets",
                "delete",
                name,
                *self._base_args(scope=scope, vmid=vmid, group=group),
            ],
            check=False,
        )

class VaultKvAdapter(_BaseNamedAdapter):
    name = "vault-kv"

    def mount(self) -> str:
        return os.environ.get("PROXNIX_VAULT_MOUNT", "secret").strip() or "secret"

    def _run(self, args: list[str], *, input_text: str | None = None, check: bool = True):
        return run_command(["vault", *args], input_text=input_text, check=check)

    def _list_payload_keys(self, payload: object) -> list[str]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, str)]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [item for item in data if isinstance(item, str)]
            if isinstance(data, dict):
                keys = data.get("keys")
                if isinstance(keys, list):
                    return [item for item in keys if isinstance(item, str)]
        return []

    def list(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        target = self.scope_path(scope=scope, vmid=vmid, group=group) + "/"
        completed = self._run(
            ["kv", "list", "-format=json", f"-mount={self.mount()}", target],
            check=False,
        )
        if completed.returncode != 0:
            return []
        payload = json.loads(completed.stdout or "[]")
        names = [name.rstrip("/") for name in self._list_payload_keys(payload)]
        return sorted(set(filter(None, names)))

    def get(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str | None:
        target = self.secret_path(scope=scope, vmid=vmid, group=group, name=name)
        completed = self._run(
            ["kv", "get", "-field=value", f"-mount={self.mount()}", target],
            check=False,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout

    def set(self, *, scope: str, vmid: str | None, group: str | None, name: str, value: str) -> None:
        target = self.secret_path(scope=scope, vmid=vmid, group=group, name=name)
        self._run(
            ["kv", "put", f"-mount={self.mount()}", target, "value=-"],
            input_text=value,
        )

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        target = self.secret_path(scope=scope, vmid=vmid, group=group, name=name)
        self._run(
            ["kv", "metadata", "delete", f"-mount={self.mount()}", target],
            check=False,
        )

    def export_scope(self, *, scope: str, vmid: str | None, group: str | None) -> dict[str, str]:
        data: dict[str, str] = {}
        for name in self.list(scope=scope, vmid=vmid, group=group):
            value = self.get(scope=scope, vmid=vmid, group=group, name=name)
            if value is not None:
                data[name] = value
        return data

def _adapter_for_name(name: str) -> _BaseNamedAdapter:
    if name == "pass":
        return PassAdapter()
    if name == "gopass":
        return GoPassAdapter()
    if name == "passhole":
        return PassholeAdapter()
    if name == "pykeepass":
        return PyKeePassAdapter()
    if name in {"keepassxc", "keepassxc-cli"}:
        return KeePassXCCliAdapter()
    if name in {"op", "1password", "onepassword"}:
        return OnePasswordAdapter()
    if name in {"bws", "bitwarden-secrets"}:
        return BitwardenSecretsAdapter()
    if name == "infisical":
        return InfisicalAdapter()
    if name in {"vault", "vault-kv"}:
        return VaultKvAdapter()
    raise AdapterError(f"unsupported named secret provider adapter: {name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proxnix secret provider adapter")
    parser.add_argument("backend")
    parser.add_argument("operation")
    parser.add_argument("--scope", required=False)
    parser.add_argument("--vmid")
    parser.add_argument("--group")
    parser.add_argument("--name")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        adapter = _adapter_for_name(args.backend)
        if args.operation == "capabilities":
            return _json_ok(capabilities=adapter.capabilities())

        if not args.scope:
            raise AdapterError("--scope is required")

        if args.operation == "list":
            return _json_ok(names=adapter.list(scope=args.scope, vmid=args.vmid, group=args.group))
        if args.operation == "get":
            if not args.name:
                raise AdapterError("--name is required for get")
            value = adapter.get(scope=args.scope, vmid=args.vmid, group=args.group, name=args.name)
            if value is None:
                return _json_ok(found=False)
            return _json_ok(found=True, value=value)
        if args.operation == "set":
            if not args.name:
                raise AdapterError("--name is required for set")
            adapter.set(
                scope=args.scope,
                vmid=args.vmid,
                group=args.group,
                name=args.name,
                value=sys.stdin.read(),
            )
            return _json_ok()
        if args.operation == "remove":
            if not args.name:
                raise AdapterError("--name is required for remove")
            adapter.remove(scope=args.scope, vmid=args.vmid, group=args.group, name=args.name)
            return _json_ok()
        if args.operation == "export-scope":
            return _json_ok(data=adapter.export_scope(scope=args.scope, vmid=args.vmid, group=args.group))
        raise AdapterError(f"unsupported operation: {args.operation}")
    except AdapterError as exc:
        return _json_error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
