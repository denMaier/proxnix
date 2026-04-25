from __future__ import annotations

import asyncio
import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path

from .errors import ProxnixWorkstationError
from .keepass_agent import derive_pykeepass_agent_password
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

    def __init__(self) -> None:
        super().__init__()

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
    default_cache_timeout_seconds = "600"

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
        else:
            cache_timeout = (
                os.environ.get("PROXNIX_PASSHOLE_CACHE_TIMEOUT", "").strip()
                or self.default_cache_timeout_seconds
            )
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
    default_cache_timeout_seconds = 600

    def __init__(self) -> None:
        super().__init__()
        self._database = None

    def _cache_timeout_seconds(self) -> int:
        raw_value = os.environ.get("PROXNIX_PYKEEPASS_CACHE_TIMEOUT", "").strip()
        if not raw_value:
            return self.default_cache_timeout_seconds
        try:
            return max(0, int(raw_value))
        except ValueError as exc:
            raise AdapterError("PROXNIX_PYKEEPASS_CACHE_TIMEOUT must be an integer number of seconds") from exc

    def _cache_enabled(self) -> bool:
        no_cache = os.environ.get("PROXNIX_PYKEEPASS_NO_CACHE", "").strip().lower()
        return no_cache not in {"1", "true", "yes"} and self._cache_timeout_seconds() > 0

    def _password_cache_path(self, database_path: str, agent_public_key: str) -> Path:
        cache_base = (
            os.environ.get("XDG_RUNTIME_DIR", "").strip()
            or os.environ.get("XDG_CACHE_HOME", "").strip()
            or str(Path.home() / ".cache")
        )
        context = os.environ.get("PROXNIX_PYKEEPASS_AGENT_CONTEXT", "").strip() or Path(database_path).name
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "database": str(Path(database_path).expanduser()),
                    "publicKey": agent_public_key.strip(),
                    "context": context,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return Path(cache_base).expanduser() / "proxnix" / "pykeepass-agent-passwords" / f"{cache_key}.json"

    def _cached_agent_password(self, database_path: str, agent_public_key: str) -> str | None:
        if not self._cache_enabled():
            return None
        cache_path = self._password_cache_path(database_path, agent_public_key)
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        password = payload.get("password")
        cached_at = payload.get("cachedAt")
        if not isinstance(password, str) or not isinstance(cached_at, (int, float)):
            return None
        if time.time() - float(cached_at) > self._cache_timeout_seconds():
            return None
        return password

    def _write_agent_password_cache(self, database_path: str, agent_public_key: str, password: str) -> None:
        if not self._cache_enabled():
            return
        cache_path = self._password_cache_path(database_path, agent_public_key)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.parent.chmod(0o700)
            cache_path.write_text(
                json.dumps({"cachedAt": time.time(), "password": password}) + "\n",
                encoding="utf-8",
            )
            cache_path.chmod(0o600)
        except OSError:
            pass

    def _agent_password(self, database_path: str, agent_public_key: str) -> str:
        cached = self._cached_agent_password(database_path, agent_public_key)
        if cached is not None:
            return cached
        password = derive_pykeepass_agent_password(database_path, agent_public_key)
        self._write_agent_password_cache(database_path, agent_public_key, password)
        return password

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
            agent_public_key = os.environ.get("PROXNIX_PYKEEPASS_AGENT_PUBLIC_KEY", "").strip()
            if not agent_public_key:
                return None
            return self._agent_password(self._database_path(), agent_public_key)
        return Path(password_file).expanduser().read_text(encoding="utf-8").rstrip("\n")

    def _pykeepass_class(self):
        try:
            module = importlib.import_module("pykeepass")
        except ImportError as exc:
            raise AdapterError(f"pykeepass is not installed for {sys.executable}") from exc
        pykeepass_class = getattr(module, "PyKeePass", None)
        if pykeepass_class is None:
            raise AdapterError("pykeepass module does not expose PyKeePass")
        return pykeepass_class

    def _open_database(self):
        if self._database is not None:
            return self._database
        pykeepass_class = self._pykeepass_class()
        try:
            self._database = pykeepass_class(
                self._database_path(),
                password=self._password(),
                keyfile=self._keyfile_path(),
            )
            return self._database
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
    name = "keepassxc"

    def capabilities(self) -> list[str]:
        return ["list", "get", "set", "remove", "export-scope"]

    def _database_path(self) -> str:
        path = _env_path("PROXNIX_KEEPASSXC_DATABASE")
        if not path:
            raise AdapterError("PROXNIX_KEEPASSXC_DATABASE is required for keepassxc provider")
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
    name = "onepassword-cli"

    def __init__(self) -> None:
        super().__init__()
        self._items_cache: dict[str, list[dict[str, object]]] = {}

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
        cache_key = self._scope_tag(scope=scope, vmid=vmid, group=group)
        if cache_key in self._items_cache:
            return list(self._items_cache[cache_key])
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
            items = [item for item in payload if isinstance(item, dict)]
            self._items_cache[cache_key] = items
            return list(items)
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
                self._items_cache.pop(scope_tag, None)
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
        self._items_cache.pop(scope_tag, None)

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        scope_tag = self._scope_tag(scope=scope, vmid=vmid, group=group)
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
                self._items_cache.pop(scope_tag, None)
                return

class OnePasswordSdkAdapter(_BaseNamedAdapter):
    name = "onepassword"

    def __init__(self) -> None:
        super().__init__()
        self._client = None
        self._vault_id: str | None = None
        self._items_cache: dict[str, list[object]] = {}

    def _vault(self) -> str:
        vault = os.environ.get("PROXNIX_1PASSWORD_VAULT", "").strip()
        if not vault:
            raise AdapterError("PROXNIX_1PASSWORD_VAULT is required for onepassword provider")
        return vault

    def _auth(self) -> str:
        configured = os.environ.get("PROXNIX_1PASSWORD_SDK_AUTH", "").strip()
        if configured:
            return configured
        token = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN", "").strip()
        if token:
            return token
        raise AdapterError(
            "PROXNIX_1PASSWORD_SDK_AUTH or OP_SERVICE_ACCOUNT_TOKEN is required for onepassword provider"
        )

    def _integration_name(self) -> str:
        return os.environ.get("PROXNIX_1PASSWORD_SDK_INTEGRATION_NAME", "proxnix").strip() or "proxnix"

    def _integration_version(self) -> str:
        return os.environ.get("PROXNIX_1PASSWORD_SDK_INTEGRATION_VERSION", "dev").strip() or "dev"

    def _run_async(self, coro):
        return asyncio.run(coro)

    def _client_module(self):
        try:
            return importlib.import_module("onepassword.client")
        except ImportError as exc:
            raise AdapterError("onepassword-sdk package is not installed") from exc

    def _types_module(self):
        try:
            return importlib.import_module("onepassword.types")
        except ImportError as exc:
            raise AdapterError("onepassword-sdk package is not installed") from exc

    def _sdk_call(self, action: str, coro):
        try:
            return self._run_async(coro)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"onepassword {action} failed: {exc}") from exc

    def _open_client(self):
        if self._client is not None:
            return self._client
        client_module = self._client_module()
        client_class = getattr(client_module, "Client", None)
        if client_class is None:
            raise AdapterError("onepassword.client does not expose Client")
        self._client = self._sdk_call(
            "authenticate",
            client_class.authenticate(
                auth=self._auth(),
                integration_name=self._integration_name(),
                integration_version=self._integration_version(),
            ),
        )
        return self._client

    def _resolve_vault_id(self) -> str:
        if self._vault_id is not None:
            return self._vault_id
        configured = self._vault()
        client = self._open_client()
        vaults = self._sdk_call("list vaults", client.vaults.list())
        for vault in vaults:
            vault_id = getattr(vault, "id", None)
            if isinstance(vault_id, str) and vault_id == configured:
                self._vault_id = vault_id
                return vault_id
        matches = [
            getattr(vault, "id", None)
            for vault in vaults
            if getattr(vault, "title", None) == configured and isinstance(getattr(vault, "id", None), str)
        ]
        if len(matches) == 1:
            self._vault_id = matches[0]
            return matches[0]
        if len(matches) > 1:
            raise AdapterError(f"multiple 1Password SDK vaults matched {configured!r}")
        raise AdapterError(f"1Password SDK vault not found: {configured}")

    def _scope_tag(self, *, scope: str, vmid: str | None, group: str | None) -> str:
        return self.scope_path(scope=scope, vmid=vmid, group=group)

    def _item_tags(self, item: object) -> list[str]:
        tags = getattr(item, "tags", None)
        if not isinstance(tags, list):
            return []
        return [tag for tag in tags if isinstance(tag, str)]

    def _item_id(self, item: object) -> str | None:
        item_id = getattr(item, "id", None)
        return item_id if isinstance(item_id, str) and item_id else None

    def _list_items(self, *, scope: str, vmid: str | None, group: str | None) -> list[object]:
        cache_key = self._scope_tag(scope=scope, vmid=vmid, group=group)
        if cache_key in self._items_cache:
            return list(self._items_cache[cache_key])
        client = self._open_client()
        items = self._sdk_call("list items", client.items.list(self._resolve_vault_id()))
        filtered = [item for item in items if cache_key in self._item_tags(item)]
        self._items_cache[cache_key] = list(filtered)
        return list(filtered)

    def _field_type_value(self, field: object) -> str:
        field_type = getattr(field, "field_type", None)
        if isinstance(field_type, str):
            return field_type
        value = getattr(field_type, "value", None)
        return value if isinstance(value, str) else str(field_type)

    def _password_field(self, item: object):
        fields = getattr(item, "fields", None)
        if not isinstance(fields, list):
            return None
        concealed_fields: list[object] = []
        for field in fields:
            field_id = getattr(field, "id", None)
            title = getattr(field, "title", None)
            if field_id == "password":
                return field
            if isinstance(title, str) and title.lower() == "password":
                return field
            if self._field_type_value(field) == "Concealed":
                concealed_fields.append(field)
        if len(concealed_fields) == 1:
            return concealed_fields[0]
        return None

    def _password_value(self, item: object) -> str | None:
        field = self._password_field(item)
        if field is None:
            return None
        value = getattr(field, "value", None)
        return value if isinstance(value, str) else None

    def _make_password_field(self, value: str):
        types_module = self._types_module()
        return types_module.ItemField(
            id="password",
            title="password",
            field_type=types_module.ItemFieldType.CONCEALED,
            value=value,
        )

    def _fetch_item(self, item_id: str):
        client = self._open_client()
        return self._sdk_call("get item", client.items.get(self._resolve_vault_id(), item_id))

    def list(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        names = [
            item["title"] if isinstance(item, dict) else getattr(item, "title", None)
            for item in self._list_items(scope=scope, vmid=vmid, group=group)
        ]
        return sorted({name for name in names if isinstance(name, str) and name})

    def get(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str | None:
        for item in self._list_items(scope=scope, vmid=vmid, group=group):
            if getattr(item, "title", None) != name:
                continue
            item_id = self._item_id(item)
            if item_id is None:
                continue
            return self._password_value(self._fetch_item(item_id))
        return None

    def set(self, *, scope: str, vmid: str | None, group: str | None, name: str, value: str) -> None:
        scope_tag = self._scope_tag(scope=scope, vmid=vmid, group=group)
        client = self._open_client()
        for item in self._list_items(scope=scope, vmid=vmid, group=group):
            if getattr(item, "title", None) != name:
                continue
            item_id = self._item_id(item)
            if item_id is None:
                continue
            full_item = self._fetch_item(item_id)
            password_field = self._password_field(full_item)
            if password_field is None:
                fields = list(getattr(full_item, "fields", []))
                fields.append(self._make_password_field(value))
                full_item.fields = fields
            else:
                password_field.value = value
            full_item.title = name
            full_item.tags = [scope_tag]
            self._sdk_call("update item", client.items.put(full_item))
            self._items_cache.pop(scope_tag, None)
            return

        types_module = self._types_module()
        params = types_module.ItemCreateParams(
            category=types_module.ItemCategory.PASSWORD,
            vault_id=self._resolve_vault_id(),
            title=name,
            tags=[scope_tag],
            fields=[self._make_password_field(value)],
        )
        self._sdk_call("create item", client.items.create(params))
        self._items_cache.pop(scope_tag, None)

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        scope_tag = self._scope_tag(scope=scope, vmid=vmid, group=group)
        client = self._open_client()
        for item in self._list_items(scope=scope, vmid=vmid, group=group):
            if getattr(item, "title", None) != name:
                continue
            item_id = self._item_id(item)
            if item_id is None:
                continue
            self._sdk_call("delete item", client.items.delete(self._resolve_vault_id(), item_id))
            self._items_cache.pop(scope_tag, None)
            return

    def export_scope(self, *, scope: str, vmid: str | None, group: str | None) -> dict[str, str]:
        data: dict[str, str] = {}
        for item in self._list_items(scope=scope, vmid=vmid, group=group):
            title = getattr(item, "title", None)
            item_id = self._item_id(item)
            if not isinstance(title, str) or item_id is None:
                continue
            value = self._password_value(self._fetch_item(item_id))
            if value is not None:
                data[title] = value
        return data

class BitwardenSecretsAdapter(_BaseNamedAdapter):
    name = "bitwarden-cli"

    def __init__(self) -> None:
        super().__init__()
        self._projects_cache: list[dict[str, object]] | None = None
        self._secrets_cache: dict[str, list[dict[str, object]]] = {}

    def _run(self, args: list[str], *, input_text: str | None = None, check: bool = True):
        return run_command(["bws", *args], input_text=input_text, check=check)

    def _project_name(self, *, scope: str, vmid: str | None, group: str | None) -> str:
        return self.scope_path(scope=scope, vmid=vmid, group=group)

    def _projects(self) -> list[dict[str, object]]:
        if self._projects_cache is not None:
            return list(self._projects_cache)
        completed = self._run(["project", "list"], check=False)
        if completed.returncode != 0:
            return []
        payload = json.loads(completed.stdout or "[]")
        if isinstance(payload, list):
            self._projects_cache = [item for item in payload if isinstance(item, dict)]
            return list(self._projects_cache)
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
        self._projects_cache = None
        return project_id

    def _list_secrets(self, *, project_id: str) -> list[dict[str, object]]:
        if project_id in self._secrets_cache:
            return list(self._secrets_cache[project_id])
        completed = self._run(["secret", "list", project_id], check=False)
        if completed.returncode != 0:
            return []
        payload = json.loads(completed.stdout or "[]")
        if isinstance(payload, list):
            items = [item for item in payload if isinstance(item, dict)]
            self._secrets_cache[project_id] = items
            return list(items)
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
                self._secrets_cache.pop(project_id, None)
                return
        self._run(["secret", "create", name, value, project_id])
        self._secrets_cache.pop(project_id, None)

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        project_id = self._project_id(scope=scope, vmid=vmid, group=group)
        if project_id is None:
            return
        for item in self._list_secrets(project_id=project_id):
            if item.get("key") == name and isinstance(item.get("id"), str):
                self._run(["secret", "delete", item["id"]], check=False)
                self._secrets_cache.pop(project_id, None)
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

class BitwardenSdkAdapter(_BaseNamedAdapter):
    name = "bitwarden"

    def __init__(self) -> None:
        super().__init__()
        self._client = None
        self._projects_cache: list[object] | None = None
        self._secrets_cache: dict[str, list[object]] = {}

    def _sdk_module(self):
        try:
            return importlib.import_module("bitwarden_sdk")
        except ImportError as exc:
            raise AdapterError("bitwarden-sdk package is not installed") from exc

    def _organization_id(self) -> str:
        organization_id = (
            os.environ.get("PROXNIX_BITWARDEN_ORGANIZATION_ID", "").strip()
            or os.environ.get("ORGANIZATION_ID", "").strip()
        )
        if not organization_id:
            raise AdapterError(
                "PROXNIX_BITWARDEN_ORGANIZATION_ID or ORGANIZATION_ID is required for bitwarden provider"
            )
        return organization_id

    def _access_token(self) -> str:
        access_token = (
            os.environ.get("PROXNIX_BITWARDEN_ACCESS_TOKEN", "").strip()
            or os.environ.get("ACCESS_TOKEN", "").strip()
        )
        if not access_token:
            raise AdapterError(
                "PROXNIX_BITWARDEN_ACCESS_TOKEN or ACCESS_TOKEN is required for bitwarden provider"
            )
        return access_token

    def _state_file(self) -> str | None:
        proxnix_state = _env_path("PROXNIX_BITWARDEN_STATE_FILE")
        if proxnix_state:
            return proxnix_state
        legacy_state = _env_path("STATE_FILE")
        return legacy_state or None

    def _api_url(self) -> str | None:
        value = os.environ.get("PROXNIX_BITWARDEN_API_URL", "").strip() or os.environ.get("API_URL", "").strip()
        return value or None

    def _identity_url(self) -> str | None:
        value = (
            os.environ.get("PROXNIX_BITWARDEN_IDENTITY_URL", "").strip()
            or os.environ.get("IDENTITY_URL", "").strip()
        )
        return value or None

    def _user_agent(self) -> str:
        return os.environ.get("PROXNIX_BITWARDEN_USER_AGENT", "proxnix").strip() or "proxnix"

    def _open_client(self):
        if self._client is not None:
            return self._client
        module = self._sdk_module()
        client_class = getattr(module, "BitwardenClient", None)
        if client_class is None:
            raise AdapterError("bitwarden_sdk does not expose BitwardenClient")
        settings = None
        if self._api_url() or self._identity_url() or self._user_agent():
            settings_factory = getattr(module, "client_settings_from_dict", None)
            device_type = getattr(module, "DeviceType", None)
            if settings_factory is None or device_type is None or not hasattr(device_type, "SDK"):
                raise AdapterError(
                    "bitwarden_sdk must expose client_settings_from_dict and DeviceType.SDK"
                )
            settings_payload = {
                "deviceType": device_type.SDK,
                "userAgent": self._user_agent(),
            }
            if self._api_url():
                settings_payload["apiUrl"] = self._api_url()
            if self._identity_url():
                settings_payload["identityUrl"] = self._identity_url()
            settings = settings_factory(settings_payload)
        try:
            self._client = client_class(settings)
            self._client.auth().login_access_token(self._access_token(), self._state_file())
            return self._client
        except Exception as exc:
            raise AdapterError(f"failed to authenticate bitwarden client: {exc}") from exc

    def _response_single(self, response):
        data = getattr(response, "data", None)
        return getattr(data, "data", data)

    def _response_items(self, response) -> list[object]:
        payload = self._response_single(response)
        return payload if isinstance(payload, list) else []

    def _project_name(self, *, scope: str, vmid: str | None, group: str | None) -> str:
        return self.scope_path(scope=scope, vmid=vmid, group=group)

    def _project_id_value(self, item: object) -> str | None:
        project_id = getattr(item, "id", None)
        return project_id if isinstance(project_id, str) and project_id else None

    def _project_name_value(self, item: object) -> str | None:
        name = getattr(item, "name", None)
        return name if isinstance(name, str) and name else None

    def _secret_id_value(self, item: object) -> str | None:
        secret_id = getattr(item, "id", None)
        return secret_id if isinstance(secret_id, str) and secret_id else None

    def _secret_key_value(self, item: object) -> str | None:
        key = getattr(item, "key", None)
        return key if isinstance(key, str) and key else None

    def _secret_value(self, item: object) -> str | None:
        value = getattr(item, "value", None)
        return value if isinstance(value, str) else None

    def _secret_project_ids(self, item: object) -> list[str]:
        project_ids = getattr(item, "project_ids", None)
        if isinstance(project_ids, list):
            return [project_id for project_id in project_ids if isinstance(project_id, str) and project_id]
        project_id = getattr(item, "project_id", None)
        if isinstance(project_id, str) and project_id:
            return [project_id]
        projects = getattr(item, "projects", None)
        if isinstance(projects, list):
            result: list[str] = []
            for project in projects:
                nested_id = getattr(project, "id", None)
                if isinstance(nested_id, str) and nested_id:
                    result.append(nested_id)
            return result
        return []

    def _projects(self) -> list[object]:
        if self._projects_cache is not None:
            return list(self._projects_cache)
        client = self._open_client()
        try:
            response = client.projects().list(self._organization_id())
        except Exception as exc:
            raise AdapterError(f"bitwarden project list failed: {exc}") from exc
        self._projects_cache = self._response_items(response)
        return list(self._projects_cache)

    def _project_id(self, *, scope: str, vmid: str | None, group: str | None) -> str | None:
        expected = self._project_name(scope=scope, vmid=vmid, group=group)
        for item in self._projects():
            if self._project_name_value(item) == expected:
                return self._project_id_value(item)
        return None

    def _ensure_project_id(self, *, scope: str, vmid: str | None, group: str | None) -> str:
        project_id = self._project_id(scope=scope, vmid=vmid, group=group)
        if project_id is not None:
            return project_id
        client = self._open_client()
        try:
            response = client.projects().create(
                self._organization_id(),
                self._project_name(scope=scope, vmid=vmid, group=group),
            )
        except Exception as exc:
            raise AdapterError(f"bitwarden project create failed: {exc}") from exc
        project = self._response_single(response)
        project_id = self._project_id_value(project)
        if project_id is None:
            raise AdapterError("bitwarden project create did not return a project id")
        self._projects_cache = None
        return project_id

    def _list_secrets(self, *, project_id: str) -> list[object]:
        if project_id in self._secrets_cache:
            return list(self._secrets_cache[project_id])
        client = self._open_client()
        try:
            identifiers = self._response_items(client.secrets().list(self._organization_id()))
        except Exception as exc:
            raise AdapterError(f"bitwarden secret list failed: {exc}") from exc
        ids = [secret_id for item in identifiers if (secret_id := self._secret_id_value(item)) is not None]
        if not ids:
            self._secrets_cache[project_id] = []
            return []
        try:
            full_items = self._response_items(client.secrets().get_by_ids(ids))
        except Exception as exc:
            raise AdapterError(f"bitwarden secret bulk fetch failed: {exc}") from exc
        filtered = [item for item in full_items if project_id in self._secret_project_ids(item)]
        self._secrets_cache[project_id] = filtered
        return list(filtered)

    def list(self, *, scope: str, vmid: str | None, group: str | None) -> list[str]:
        project_id = self._project_id(scope=scope, vmid=vmid, group=group)
        if project_id is None:
            return []
        return sorted(
            {
                key
                for item in self._list_secrets(project_id=project_id)
                if (key := self._secret_key_value(item)) is not None
            }
        )

    def get(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> str | None:
        project_id = self._project_id(scope=scope, vmid=vmid, group=group)
        if project_id is None:
            return None
        for item in self._list_secrets(project_id=project_id):
            if self._secret_key_value(item) == name:
                return self._secret_value(item)
        return None

    def set(self, *, scope: str, vmid: str | None, group: str | None, name: str, value: str) -> None:
        project_id = self._ensure_project_id(scope=scope, vmid=vmid, group=group)
        client = self._open_client()
        for item in self._list_secrets(project_id=project_id):
            secret_id = self._secret_id_value(item)
            if self._secret_key_value(item) == name and secret_id is not None:
                try:
                    client.secrets().update(
                        self._organization_id(),
                        secret_id,
                        name,
                        value,
                        None,
                        [project_id],
                    )
                except Exception as exc:
                    raise AdapterError(f"bitwarden secret update failed: {exc}") from exc
                self._secrets_cache.pop(project_id, None)
                return
        try:
            client.secrets().create(
                self._organization_id(),
                name,
                value,
                None,
                [project_id],
            )
        except Exception as exc:
            raise AdapterError(f"bitwarden secret create failed: {exc}") from exc
        self._secrets_cache.pop(project_id, None)

    def remove(self, *, scope: str, vmid: str | None, group: str | None, name: str) -> None:
        project_id = self._project_id(scope=scope, vmid=vmid, group=group)
        if project_id is None:
            return
        client = self._open_client()
        for item in self._list_secrets(project_id=project_id):
            secret_id = self._secret_id_value(item)
            if self._secret_key_value(item) == name and secret_id is not None:
                try:
                    client.secrets().delete([secret_id])
                except Exception as exc:
                    raise AdapterError(f"bitwarden secret delete failed: {exc}") from exc
                self._secrets_cache.pop(project_id, None)
                return

    def export_scope(self, *, scope: str, vmid: str | None, group: str | None) -> dict[str, str]:
        project_id = self._project_id(scope=scope, vmid=vmid, group=group)
        if project_id is None:
            return {}
        data: dict[str, str] = {}
        for item in self._list_secrets(project_id=project_id):
            key = self._secret_key_value(item)
            value = self._secret_value(item)
            if key is not None and value is not None:
                data[key] = value
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
    if name == "keepassxc":
        return KeePassXCCliAdapter()
    if name == "onepassword":
        return OnePasswordSdkAdapter()
    if name == "onepassword-cli":
        return OnePasswordAdapter()
    if name == "bitwarden-cli":
        return BitwardenSecretsAdapter()
    if name == "bitwarden":
        return BitwardenSdkAdapter()
    raise AdapterError(f"unsupported named secret provider adapter: {name}")


def create_named_adapter(name: str) -> _BaseNamedAdapter:
    return _adapter_for_name(name)


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
