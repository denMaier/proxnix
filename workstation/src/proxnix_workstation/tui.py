#!/usr/bin/env python3
"""Terminal UI for proxnix workstation workflows.

Mirrors the structure and interaction model of the ProxnixManager GUI: sidebar
with sections (Actions / Containers / App), container detail view with status
tiles, Doctor screen, Settings editor, and color-coded output.
"""

from __future__ import annotations

import argparse
import curses
import os
import re
import select
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from proxnix_workstation.config import load_workstation_config as load_python_config
from proxnix_workstation.git_ops import GitStatus, git_init, git_status, git_diff_summary, git_stage_all, git_commit, git_push
from proxnix_workstation.paths import SitePaths
from proxnix_workstation.provider_keys import have_container_private_key
from proxnix_workstation.secret_provider import load_secret_provider
from proxnix_workstation.secret_provider_types import group_scope
from proxnix_workstation.site import collect_site_vmids, read_container_secret_groups

PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_DIR.parent.parent if PACKAGE_DIR.parent.name == "src" else None
WORKSTATION_DIR = SOURCE_ROOT if SOURCE_ROOT and (SOURCE_ROOT / "pyproject.toml").is_file() else None
COMMON_SCRIPT = None if WORKSTATION_DIR is None else WORKSTATION_DIR / "legacy" / "proxnix-workstation-common.sh"
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "proxnix" / "config"


# ─── Command Resolution ────────────────────────────────────────


def _bin_dir_candidates() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("PROXNIX_WORKSTATION_BIN_DIR")
    if explicit:
        candidates.append(Path(explicit).expanduser())

    argv0 = Path(sys.argv[0]).expanduser()
    if argv0.name:
        candidates.append(argv0.resolve().parent)

    if WORKSTATION_DIR is not None:
        candidates.append(WORKSTATION_DIR / "bin")

    return candidates


def resolve_command_path(name: str) -> Path | None:
    for directory in _bin_dir_candidates():
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    resolved = shutil.which(name)
    if resolved:
        return Path(resolved)
    return None


# ─── Data Classes ───────────────────────────────────────────────


@dataclass
class Config:
    site_dir: str = ""
    hosts: list[str] = field(default_factory=list)
    ssh_identity: str = ""
    remote_dir: str = "/var/lib/proxnix"
    remote_priv_dir: str = "/var/lib/proxnix/private"
    remote_host_relay_identity: str = "/etc/proxnix/host_relay_identity"


@dataclass
class ContainerInfo:
    vmid: str
    has_config: bool
    dropins: list[str]
    groups: list[str]
    has_secret_store: bool
    has_identity: bool


@dataclass
class PublishState:
    dry_run: bool = False
    config_only: bool = False
    report_changes: bool = True
    vmid: str = ""
    host_override: str = ""


@dataclass
class SecretsState:
    vmid: str = ""
    name: str = ""
    group: str = ""
    mode: str = "groups"  # "groups" or "containers"


@dataclass
class DoctorState:
    site_only: bool = True
    host_only: bool = False
    config_only: bool = False
    target_vmid: str = ""
    sections: list[DoctorSection] = field(default_factory=list)
    has_run: bool = False
    filter_level: str = ""  # "", "OK", "INFO", "WARN", "FAIL"


@dataclass
class DoctorSection:
    name: str
    entries: list[DoctorEntry] = field(default_factory=list)


@dataclass
class DoctorEntry:
    level: str  # "OK", "INFO", "WARN", "FAIL"
    message: str


@dataclass
class GitState:
    status: GitStatus = field(default_factory=GitStatus)
    commit_message: str = ""
    has_refreshed: bool = False


@dataclass
class SidebarItem:
    label: str
    kind: str  # "action", "container", "app", "header"
    key: str
    icon: str = ""


@dataclass
class AppState:
    config: Config = field(default_factory=Config)
    containers: list[ContainerInfo] = field(default_factory=list)
    publish: PublishState = field(default_factory=PublishState)
    secrets: SecretsState = field(default_factory=SecretsState)
    doctor: DoctorState = field(default_factory=DoctorState)
    git: GitState = field(default_factory=GitState)
    status: str = ""
    command_output: str = ""
    command_title: str = ""
    scroll: int = 0

    def refresh(self) -> None:
        self.config = load_config()
        self.containers = scan_site(self.config.site_dir)
        self.status = f"Loaded {len(self.containers)} container(s)"


# ─── Config Loading ─────────────────────────────────────────────


def load_config() -> Config:
    try:
        loaded = load_python_config(CONFIG_FILE)
    except Exception:
        loaded = None
    if loaded is not None:
        return Config(
            site_dir="" if loaded.site_dir is None else str(loaded.site_dir),
            hosts=list(loaded.hosts),
            ssh_identity="" if loaded.ssh_identity is None else str(loaded.ssh_identity),
            remote_dir=str(loaded.remote_dir),
            remote_priv_dir=str(loaded.remote_priv_dir),
            remote_host_relay_identity=str(loaded.remote_host_relay_identity),
        )

    if COMMON_SCRIPT is None or not COMMON_SCRIPT.exists():
        return Config()

    script = f"""
set -e
source {shell_quote(str(COMMON_SCRIPT))}
load_proxnix_workstation_config
printf 'PROXNIX_SITE_DIR=%s\\n' "$PROXNIX_SITE_DIR"
printf 'PROXNIX_HOSTS=%s\\n' "$PROXNIX_HOSTS"
printf 'PROXNIX_SSH_IDENTITY=%s\\n' "$PROXNIX_SSH_IDENTITY"
printf 'PROXNIX_REMOTE_DIR=%s\\n' "$PROXNIX_REMOTE_DIR"
printf 'PROXNIX_REMOTE_PRIV_DIR=%s\\n' "$PROXNIX_REMOTE_PRIV_DIR"
printf 'PROXNIX_REMOTE_HOST_RELAY_IDENTITY=%s\\n' "$PROXNIX_REMOTE_HOST_RELAY_IDENTITY"
"""

    result = subprocess.run(
        ["/bin/bash", "-lc", script],
        text=True,
        capture_output=True,
        cwd=None if WORKSTATION_DIR is None else str(WORKSTATION_DIR),
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        return Config()

    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value

    return Config(
        site_dir=values.get("PROXNIX_SITE_DIR", ""),
        hosts=values.get("PROXNIX_HOSTS", "").split(),
        ssh_identity=values.get("PROXNIX_SSH_IDENTITY", ""),
        remote_dir=values.get("PROXNIX_REMOTE_DIR", "/var/lib/proxnix"),
        remote_priv_dir=values.get("PROXNIX_REMOTE_PRIV_DIR", "/var/lib/proxnix/private"),
        remote_host_relay_identity=values.get(
            "PROXNIX_REMOTE_HOST_RELAY_IDENTITY", "/etc/proxnix/host_relay_identity"
        ),
    )


# ─── Site Scanning ──────────────────────────────────────────────


def _load_tui_provider(site_dir: str) -> tuple[object, SitePaths, object] | None:
    """Try to load the workstation config and secret provider for identity/group checks."""
    try:
        config = load_python_config()
        site_paths = SitePaths.from_config(config)
        provider = load_secret_provider(config, site_paths)
        return config, site_paths, provider
    except Exception:
        return None


def _container_has_identity(ctx: tuple[object, SitePaths, object] | None, vmid: str, private_container: Path) -> bool:
    if ctx is not None:
        try:
            config, site_paths, provider = ctx
            return have_container_private_key(config, provider, site_paths, vmid)
        except Exception:
            pass
    return (private_container / "age_identity.sops.yaml").is_file()


def _container_has_secrets(ctx: tuple[object, SitePaths, object] | None, groups: list[str], private_container: Path) -> bool:
    if ctx is not None and groups:
        try:
            _, _, provider = ctx
            return any(provider.has_any(group_scope(g)) for g in groups)
        except Exception:
            pass
    return (private_container / "secrets.sops.yaml").is_file()


def scan_site(site_dir: str) -> list[ContainerInfo]:
    if not site_dir:
        return []

    root = Path(site_dir).expanduser()
    ctx = _load_tui_provider(site_dir)

    try:
        site_paths = SitePaths(root)
        vmids = collect_site_vmids(site_paths)

        def build(vmid: str) -> ContainerInfo:
            public_dir = site_paths.container_dir(vmid)
            private_container = site_paths.private_dir / "containers" / vmid
            dropin_dir = public_dir / "dropins"
            groups = read_container_secret_groups(site_paths, vmid)
            dropins = sorted(p.name for p in dropin_dir.iterdir()) if dropin_dir.is_dir() else []
            return ContainerInfo(
                vmid=vmid,
                has_config=public_dir.exists(),
                dropins=dropins,
                groups=groups,
                has_secret_store=_container_has_secrets(ctx, groups, private_container),
                has_identity=_container_has_identity(ctx, vmid, private_container),
            )

        return [build(vmid) for vmid in vmids]
    except Exception:
        pass

    containers_dir = root / "containers"
    private_dir = root / "private" / "containers"
    vmids: set[str] = set()

    for base in (containers_dir, private_dir):
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if entry.is_dir() and entry.name.isdigit():
                vmids.add(entry.name)

    def build(vmid: str) -> ContainerInfo:
        public_dir = containers_dir / vmid
        private_container = private_dir / vmid
        dropin_dir = public_dir / "dropins"
        groups_file = public_dir / "secret-groups.list"
        groups: list[str] = []
        if groups_file.exists():
            for raw in groups_file.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    groups.append(line)

        dropins = sorted(p.name for p in dropin_dir.iterdir()) if dropin_dir.is_dir() else []
        return ContainerInfo(
            vmid=vmid,
            has_config=public_dir.exists(),
            dropins=dropins,
            groups=groups,
            has_secret_store=_container_has_secrets(ctx, groups, private_container),
            has_identity=_container_has_identity(ctx, vmid, private_container),
        )

    return [build(vmid) for vmid in sorted(vmids, key=int)]


# ─── Shell Helpers ──────────────────────────────────────────────


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


_ANSI_RE = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# ─── Theme & Colors ────────────────────────────────────────────

# Color pair IDs
C_ACCENT = 1
C_OK = 2
C_WARN = 3
C_FAIL = 4
C_INFO = 5
C_DIM = 6
C_HEADER = 7
C_SIDEBAR_HEADER = 8

_colors_available = False


def setup_theme() -> None:
    global _colors_available
    try:
        curses.start_color()
        curses.use_default_colors()
        # Accent — electric teal/cyan
        curses.init_pair(C_ACCENT, curses.COLOR_CYAN, -1)
        # Status palette
        curses.init_pair(C_OK, curses.COLOR_GREEN, -1)
        curses.init_pair(C_WARN, curses.COLOR_YELLOW, -1)
        curses.init_pair(C_FAIL, curses.COLOR_RED, -1)
        curses.init_pair(C_INFO, curses.COLOR_BLUE, -1)
        # Dim / secondary
        curses.init_pair(C_DIM, 244 if curses.COLORS >= 256 else curses.COLOR_WHITE, -1)
        # Header bar — accent background
        curses.init_pair(C_HEADER, curses.COLOR_BLACK, curses.COLOR_CYAN)
        # Sidebar section headers
        curses.init_pair(C_SIDEBAR_HEADER, curses.COLOR_CYAN, -1)
        _colors_available = True
    except curses.error:
        _colors_available = False


def color(pair: int, extra: int = 0) -> int:
    if not _colors_available:
        return extra
    return curses.color_pair(pair) | extra


def level_color(level: str) -> int:
    return {
        "OK": color(C_OK),
        "INFO": color(C_INFO),
        "WARN": color(C_WARN),
        "FAIL": color(C_FAIL),
    }.get(level, 0)


def level_icon(level: str) -> str:
    return {"OK": "\u2714", "INFO": "\u2139", "WARN": "\u26a0", "FAIL": "\u2718"}.get(level, " ")


# ─── Drawing Primitives ────────────────────────────────────────


def safe_addnstr(win: curses.window, y: int, x: int, text: str, limit: int, attr: int = 0) -> None:
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w or limit <= 0:
        return
    width = min(limit, w - x)
    if width <= 0:
        return
    try:
        win.addnstr(y, x, text, width, attr)
    except curses.error:
        pass


def safe_hline(win: curses.window, y: int, x: int, ch: int, count: int) -> None:
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w or count <= 0:
        return
    try:
        win.hline(y, x, ch, min(count, w - x))
    except curses.error:
        pass


def safe_vline(win: curses.window, y: int, x: int, ch: int, count: int) -> None:
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w or count <= 0:
        return
    try:
        win.vline(y, x, ch, min(count, h - y))
    except curses.error:
        pass


def safe_move(win: curses.window, y: int, x: int) -> None:
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    try:
        win.move(y, x)
    except curses.error:
        pass


def read_key(win: curses.window):
    if hasattr(win, "get_wch"):
        return win.get_wch()
    key = win.getch()
    if key == -1:
        return ""
    if key in {
        curses.KEY_UP,
        curses.KEY_DOWN,
        curses.KEY_LEFT,
        curses.KEY_RIGHT,
        curses.KEY_BACKSPACE,
        curses.KEY_NPAGE,
        curses.KEY_PPAGE,
    }:
        return key
    if key == 9:
        return "\t"
    if key in (10, 13):
        return "\n"
    if key == 27:
        return "\x1b"
    if 0 <= key < 256:
        return chr(key)
    return key


def wrap_lines(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines() or [""]:
        wrapped = textwrap.wrap(raw, width=max(10, width)) or [""]
        lines.extend(wrapped)
    return lines


def draw_header_bar(win: curses.window, title: str, subtitle: str = "") -> None:
    """Draw a colored title bar at the top of a pane."""
    h, w = win.getmaxyx()
    bar = f" {title} "
    safe_addnstr(win, 0, 0, bar.ljust(w), w, color(C_HEADER, curses.A_BOLD))
    if subtitle:
        safe_addnstr(win, 1, 1, subtitle, w - 2, color(C_DIM))
    if h > 2:
        safe_hline(win, 2, 0, curses.ACS_HLINE, w)


def draw_eyebrow(win: curses.window, y: int, x: int, text: str, w: int) -> None:
    """Draw an eyebrow section label like the app's EyebrowLabel."""
    label = f"\u25aa {text.upper()}"
    safe_addnstr(win, y, x, label, w, color(C_ACCENT, curses.A_BOLD))


def draw_section(win: curses.window, y: int, x: int, w: int, eyebrow: str, title: str = "",
                 trailing: str = "") -> int:
    """Draw a section header with eyebrow + title. Returns next y."""
    draw_eyebrow(win, y, x, eyebrow, w)
    if trailing:
        tlen = len(trailing) + 2
        safe_addnstr(win, y, x + w - tlen, trailing, tlen, color(C_DIM))
    if title:
        safe_addnstr(win, y + 1, x, title, w, curses.A_BOLD)
        return y + 2
    return y + 1


def draw_tile(win: curses.window, y: int, x: int, tw: int, icon: str, value: str,
              label: str, clr: int) -> None:
    """Draw a small metric tile like the app's statusTile."""
    # Top border
    safe_addnstr(win, y, x, "\u250c" + "\u2500" * (tw - 2) + "\u2510", tw, color(C_DIM))
    # Icon
    safe_addnstr(win, y + 1, x, "\u2502", 1, color(C_DIM))
    safe_addnstr(win, y + 1, x + 2, icon, tw - 4, clr)
    safe_addnstr(win, y + 1, x + tw - 1, "\u2502", 1, color(C_DIM))
    # Value
    safe_addnstr(win, y + 2, x, "\u2502", 1, color(C_DIM))
    safe_addnstr(win, y + 2, x + 2, value, tw - 4, clr | curses.A_BOLD)
    safe_addnstr(win, y + 2, x + tw - 1, "\u2502", 1, color(C_DIM))
    # Label
    safe_addnstr(win, y + 3, x, "\u2502", 1, color(C_DIM))
    safe_addnstr(win, y + 3, x + 2, label, tw - 4, color(C_DIM))
    safe_addnstr(win, y + 3, x + tw - 1, "\u2502", 1, color(C_DIM))
    # Bottom border
    safe_addnstr(win, y + 4, x, "\u2514" + "\u2500" * (tw - 2) + "\u2518", tw, color(C_DIM))


def draw_checkbox(win: curses.window, y: int, x: int, label: str, checked: bool,
                  selected: bool, w: int) -> None:
    """Draw a toggle/checkbox like the app's Toggle controls."""
    check = "\u2714" if checked else " "
    prefix = "[" + check + "] "
    attr = curses.A_REVERSE if selected else 0
    c = color(C_ACCENT) if checked else 0
    safe_addnstr(win, y, x, prefix, len(prefix), c | attr)
    safe_addnstr(win, y, x + len(prefix), label, w - len(prefix), attr)


def draw_status_badge(win: curses.window, y: int, x: int, text: str, clr: int, w: int) -> None:
    """Draw a compact status badge like the app's capsule badges."""
    badge = f" {text} "
    safe_addnstr(win, y, x, badge, min(len(badge), w), clr | curses.A_BOLD)


# ─── Prompts ────────────────────────────────────────────────────


def prompt_text(stdscr: curses.window, title: str, initial: str = "", secret: bool = False) -> str | None:
    curses.curs_set(1)
    value = list(initial)
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        draw_header_bar(stdscr, title, "Enter to confirm, Esc to cancel")
        label = "Value: "
        shown = "*" * len(value) if secret else "".join(value)
        y = min(h // 2, h - 3)
        safe_addnstr(stdscr, y, 2, label + shown, max(0, w - 4))
        safe_move(stdscr, y, min(w - 2, 2 + len(label) + len(shown)))
        stdscr.refresh()

        key = read_key(stdscr)
        if key in ("\n", "\r"):
            curses.curs_set(0)
            return "".join(value).strip()
        if key == "\x1b":
            curses.curs_set(0)
            return None
        if key in ("\b", "\x7f") or key == curses.KEY_BACKSPACE:
            if value:
                value.pop()
            continue
        if isinstance(key, str) and key.isprintable():
            value.append(key)


def confirm(stdscr: curses.window, title: str, question: str) -> bool:
    while True:
        stdscr.clear()
        draw_header_bar(stdscr, title, "Y to confirm, N or Esc to cancel")
        for idx, line in enumerate(wrap_lines(question, stdscr.getmaxyx()[1] - 4)):
            safe_addnstr(stdscr, 4 + idx, 2, line, stdscr.getmaxyx()[1] - 4)
        stdscr.refresh()
        key = read_key(stdscr)
        if key in ("y", "Y"):
            return True
        if key in ("n", "N", "\x1b"):
            return False


# ─── Command Execution ─────────────────────────────────────────


def run_script_streaming(
    stdscr: curses.window,
    app: AppState,
    title: str,
    script_name: str,
    args: list[str],
    stdin_text: str | None = None,
) -> None:
    """Run a script and stream output to the Output screen in real time."""
    script_path = resolve_command_path(script_name)
    header = f"$ {script_name} {' '.join(args)}".rstrip()
    app.command_title = title
    app.command_output = header + "\n\n"
    app.scroll = 0

    if script_path is None:
        app.command_output += f"error: command not found on PATH: {script_name}\n"
        app.status = f"{title}: script not found"
        return

    env = os.environ.copy()
    command_bin_dir = str(script_path.parent)
    env["PROXNIX_WORKSTATION_BIN_DIR"] = command_bin_dir
    env["PATH"] = f"{command_bin_dir}:{env.get('PATH', '')}" if env.get("PATH") else command_bin_dir

    proc = subprocess.Popen(
        [str(script_path), *args],
        cwd=None if WORKSTATION_DIR is None else str(WORKSTATION_DIR),
        env=env,
        stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if stdin_text is not None and proc.stdin is not None:
        proc.stdin.write(stdin_text.encode())
        proc.stdin.close()

    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)

    stdscr.nodelay(True)
    try:
        while proc.poll() is None:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                chunk = proc.stdout.read(4096)
                if chunk:
                    app.command_output += chunk.decode(errors="replace")
            _draw_streaming_output(stdscr, app)
            try:
                ch = stdscr.getch()
                if ch in (ord("q"), ord("Q"), 27):  # q, Q, Esc
                    proc.terminate()
                    app.command_output += "\n[cancelled by user]\n"
                    break
            except curses.error:
                pass
        remaining = proc.stdout.read()
        if remaining:
            app.command_output += remaining.decode(errors="replace")
    finally:
        stdscr.nodelay(False)
        proc.stdout.close()

    code = proc.wait()
    app.command_output = app.command_output.rstrip() + f"\n\nexit code: {code}\n"
    ok = code == 0
    app.status = f"{title}: {'success' if ok else f'failed (exit {code})'}"


def _draw_streaming_output(stdscr: curses.window, app: AppState) -> None:
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    # Title bar
    title = app.command_title or "Running"
    safe_addnstr(stdscr, 0, 0, f" {title} (running...) ".ljust(w), w, color(C_HEADER, curses.A_BOLD))
    safe_addnstr(stdscr, 1, 1, "q/Esc to cancel", w - 2, color(C_DIM))
    safe_hline(stdscr, 2, 0, curses.ACS_HLINE, w)

    lines = strip_ansi(app.command_output).splitlines()
    visible_rows = max(1, h - 4)
    start = max(0, len(lines) - visible_rows)
    for idx, line in enumerate(lines[start : start + visible_rows]):
        safe_addnstr(stdscr, 3 + idx, 1, line, w - 2)

    # Status bar
    status = app.status or f"{len(lines)} lines"
    safe_addnstr(stdscr, h - 1, 0, f" {status} ".ljust(w), w, color(C_HEADER))
    stdscr.refresh()


def run_command(
    stdscr: curses.window,
    app: AppState,
    title: str,
    script_name: str,
    args: list[str],
    stdin_text: str | None = None,
) -> None:
    run_script_streaming(stdscr, app, title, script_name, args, stdin_text=stdin_text)


# ─── Sidebar ───────────────────────────────────────────────────


def build_sidebar_items(app: AppState) -> list[SidebarItem]:
    items: list[SidebarItem] = []
    # Actions section
    items.append(SidebarItem("ACTIONS", "header", "_h_actions", "\u26a1"))
    items.append(SidebarItem("Git", "action", "git", "\u2387"))
    items.append(SidebarItem("Doctor", "action", "doctor", "\u2695"))
    items.append(SidebarItem("Publish All", "action", "publish", "\u2191"))
    items.append(SidebarItem("Secrets", "action", "secrets", "\u26bf"))
    # Containers section
    ct_count = len(app.containers)
    items.append(SidebarItem(f"CONTAINERS ({ct_count})", "header", "_h_containers", "\u25a3"))
    if app.containers:
        for c in app.containers:
            dot = "\u25cf" if c.has_secret_store else "\u25cb"
            label = f"{dot} {c.vmid}"
            items.append(SidebarItem(label, "container", c.vmid, ""))
    else:
        items.append(SidebarItem("  (no containers)", "empty", "_empty", ""))
    # App section
    items.append(SidebarItem("APP", "header", "_h_app", "\u2699"))
    items.append(SidebarItem("Settings", "app", "settings", "\u2699"))
    items.append(SidebarItem("Help", "app", "help", "?"))
    return items


def draw_sidebar(stdscr: curses.window, items: list[SidebarItem], selected: int) -> int:
    """Draw the sidebar and return its width."""
    h, w = stdscr.getmaxyx()
    sidebar_w = max(22, min(30, w // 4))

    # Sidebar border
    safe_vline(stdscr, 0, sidebar_w, curses.ACS_VLINE, h)

    # Title
    safe_addnstr(stdscr, 0, 1, "Proxnix", sidebar_w - 2, color(C_ACCENT, curses.A_BOLD))

    row = 2
    for idx, item in enumerate(items):
        if row >= h - 1:
            break
        if item.kind == "header":
            # Section header
            if row > 2:
                row += 1  # spacing before section
            if row >= h - 1:
                break
            safe_addnstr(stdscr, row, 1, item.label, sidebar_w - 2,
                         color(C_SIDEBAR_HEADER, curses.A_BOLD))
            row += 1
        elif item.kind == "empty":
            safe_addnstr(stdscr, row, 1, item.label, sidebar_w - 2, color(C_DIM))
            row += 1
        else:
            is_sel = idx == selected
            attr = curses.A_REVERSE if is_sel else 0
            if is_sel:
                attr |= curses.A_BOLD
            # Indent selectable items
            label = f"  {item.label}"
            safe_addnstr(stdscr, row, 1, label[:sidebar_w - 2].ljust(sidebar_w - 2),
                         sidebar_w - 2, attr)
            row += 1

    return sidebar_w


def selectable_indices(items: list[SidebarItem]) -> list[int]:
    """Return indices of items that can be selected (not headers or empty)."""
    return [i for i, item in enumerate(items) if item.kind not in ("header", "empty")]


# ─── Screen: Container Detail ──────────────────────────────────


def render_container_detail(win: curses.window, app: AppState, vmid: str) -> None:
    """Render the container detail view."""
    h, w = win.getmaxyx()
    container = next((c for c in app.containers if c.vmid == vmid), None)
    if container is None:
        draw_header_bar(win, f"Container {vmid}", "Container not found")
        safe_addnstr(win, 4, 2, "Refresh or choose a different container.", w - 4, color(C_DIM))
        return

    draw_header_bar(win, f"Container {vmid}", "Enter to run actions, r to refresh")

    y = 4
    # ── Header card ──
    dropin_count = len(container.dropins)
    dropin_label = f"{dropin_count} drop-in{'s' if dropin_count != 1 else ''} configured"
    draw_eyebrow(win, y, 2, "Container", w - 4)
    y += 1
    safe_addnstr(win, y, 2, vmid, w - 4, curses.A_BOLD)
    y += 1
    safe_addnstr(win, y, 2, dropin_label, w - 4, color(C_DIM))
    y += 2

    # ── Status tiles ──
    tile_w = max(14, min(18, (w - 8) // 4))
    tiles = [
        ("\u26bf", "Present" if container.has_identity else "Missing", "Identity",
         color(C_WARN) if container.has_identity else color(C_DIM)),
        ("\u26bf", "Ready" if container.has_secret_store else "Missing", "Secrets",
         color(C_OK) if container.has_secret_store else color(C_DIM)),
        ("\u2630", str(len(container.groups)) if container.groups else "None", "Groups",
         color(C_INFO) if container.groups else color(C_DIM)),
        ("\u2637", str(dropin_count), "Drop-ins",
         color(C_ACCENT) if container.dropins else color(C_DIM)),
    ]
    for i, (icon, val, label, clr) in enumerate(tiles):
        tx = 2 + i * (tile_w + 1)
        if tx + tile_w > w:
            break
        draw_tile(win, y, tx, tile_w, icon, val, label, clr)
    y += 6

    # ── Drop-ins section ──
    if y < h - 3:
        trailing = f"{dropin_count} file{'s' if dropin_count != 1 else ''}" if container.dropins else ""
        y = draw_section(win, y, 2, w - 4, "Files", "Drop-ins", trailing)
        y += 1
        if container.dropins:
            for dropin in container.dropins:
                if y >= h - 6:
                    safe_addnstr(win, y, 4, f"... and {len(container.dropins) - container.dropins.index(dropin)} more",
                                 w - 6, color(C_DIM))
                    y += 1
                    break
                safe_addnstr(win, y, 4, f"\u2502 {dropin}", w - 6)
                y += 1
        else:
            safe_addnstr(win, y, 4, "No drop-ins found.", w - 6, color(C_DIM))
            y += 1
        y += 1

    # ── Secret groups section ──
    if y < h - 3:
        group_count = len(container.groups)
        trailing = f"{group_count} attached" if container.groups else ""
        y = draw_section(win, y, 2, w - 4, "Access", "Secret groups", trailing)
        y += 1
        if container.groups:
            for group in container.groups:
                if y >= h - 4:
                    break
                safe_addnstr(win, y, 4, f"\u25cf {group}", w - 6, color(C_ACCENT))
                y += 1
        else:
            safe_addnstr(win, y, 4, "No groups attached.", w - 6, color(C_DIM))
            y += 1


def activate_container_detail(stdscr: curses.window, app: AppState, vmid: str) -> bool:
    """Interactive container actions. Returns True if a command was run."""
    container = next((c for c in app.containers if c.vmid == vmid), None)
    if container is None:
        app.status = f"Container {vmid} not found"
        return False

    actions = [
        ("Publish container", "publish"),
        ("Publish config only", "publish_config"),
        ("List secrets", "ls_secrets"),
        ("Get secret", "get_secret"),
        ("Set secret", "set_secret"),
        ("Init identity", "init_identity"),
        ("View dropins", "view_dropins"),
    ]
    selected = 0

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        draw_header_bar(stdscr, f"Container {vmid} \u2014 Actions", "Enter runs, Esc returns")

        # Container summary
        safe_addnstr(stdscr, 4, 2, f"VMID: {container.vmid}", w - 4)
        flags = []
        if container.has_identity:
            flags.append("identity")
        if container.has_secret_store:
            flags.append("secrets")
        if container.groups:
            flags.append(f"groups: {', '.join(container.groups)}")
        if container.dropins:
            flags.append(f"{len(container.dropins)} drop-in(s)")
        safe_addnstr(stdscr, 5, 2, " \u00b7 ".join(flags) if flags else "no state", w - 4, color(C_DIM))

        safe_hline(stdscr, 7, 2, curses.ACS_HLINE, w - 4)

        for idx, (label, _) in enumerate(actions):
            y = 9 + idx
            if y >= h - 2:
                break
            attr = curses.A_REVERSE | curses.A_BOLD if idx == selected else 0
            safe_addnstr(stdscr, y, 4, label, w - 8, attr)

        safe_addnstr(stdscr, h - 1, 0, app.status.ljust(w), w, color(C_HEADER))
        stdscr.refresh()

        key = read_key(stdscr)
        if key == curses.KEY_UP:
            selected = (selected - 1) % len(actions)
        elif key == curses.KEY_DOWN:
            selected = (selected + 1) % len(actions)
        elif key in ("\x1b", "q"):
            return False
        elif key in ("\n", "\r"):
            _, action = actions[selected]
            return _run_container_action(stdscr, app, container, action)


def _run_container_action(stdscr: curses.window, app: AppState, container: ContainerInfo,
                          action: str) -> bool:
    if action == "publish":
        run_command(stdscr, app, "Publish", "proxnix-publish",
                    ["--vmid", container.vmid, "--report-changes"])
        return True
    if action == "publish_config":
        run_command(stdscr, app, "Publish config", "proxnix-publish",
                    ["--config-only", "--vmid", container.vmid, "--report-changes"])
        return True
    if action == "ls_secrets":
        run_command(stdscr, app, "List secrets", "proxnix-secrets", ["ls", container.vmid])
        return True
    if action == "get_secret":
        name = prompt_text(stdscr, f"Secret Name for {container.vmid}", app.secrets.name)
        if not name:
            app.status = "Get secret cancelled"
            return False
        app.secrets.vmid = container.vmid
        app.secrets.name = name
        run_command(stdscr, app, "Get secret", "proxnix-secrets", ["get", container.vmid, name])
        return True
    if action == "set_secret":
        name = prompt_text(stdscr, f"Secret Name for {container.vmid}", app.secrets.name)
        if not name:
            app.status = "Set secret cancelled"
            return False
        value = prompt_text(stdscr, f"Secret Value for {container.vmid}", "", secret=True)
        if value is None or not value:
            app.status = "Set secret cancelled"
            return False
        app.secrets.vmid = container.vmid
        app.secrets.name = name
        run_command(stdscr, app, "Set secret", "proxnix-secrets",
                    ["set", container.vmid, name], stdin_text=value)
        return True
    if action == "init_identity":
        run_command(stdscr, app, "Init identity", "proxnix-secrets",
                    ["init-container", container.vmid])
        return True
    if action == "view_dropins":
        _view_dropins(stdscr, app, container)
        return bool(app.command_output)
    return False


def _view_dropins(stdscr: curses.window, app: AppState, container: ContainerInfo) -> None:
    if not container.dropins:
        app.status = f"Container {container.vmid} has no dropins"
        return

    site_dir = Path(app.config.site_dir).expanduser()
    dropin_dir = site_dir / "containers" / container.vmid / "dropins"
    selected = 0

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        draw_header_bar(stdscr, f"Drop-ins \u2014 Container {container.vmid}", "Enter to view, Esc to return")

        for idx, name in enumerate(container.dropins):
            y = 4 + idx
            if y >= h - 2:
                break
            attr = curses.A_REVERSE | curses.A_BOLD if idx == selected else 0
            icon = "\u25b8" if idx == selected else " "
            safe_addnstr(stdscr, y, 2, f"{icon} {name}", w - 4, attr)

        safe_addnstr(stdscr, h - 1, 0, app.status.ljust(w), w, color(C_HEADER))
        stdscr.refresh()
        key = read_key(stdscr)
        if key in ("\x1b", "q"):
            return
        if key == curses.KEY_UP:
            selected = (selected - 1) % len(container.dropins)
        elif key == curses.KEY_DOWN:
            selected = (selected + 1) % len(container.dropins)
        elif key in ("\n", "\r"):
            dropin_path = dropin_dir / container.dropins[selected]
            try:
                content = dropin_path.read_text(encoding="utf-8")
            except OSError as exc:
                content = f"Could not read file: {exc}"
            app.command_title = f"Drop-in: {container.dropins[selected]}"
            app.command_output = f"# {dropin_path}\n\n{content}"
            app.scroll = 0
            return


# ─── Screen: Git ────────────────────────────────────────────────


def _refresh_git_status(app: AppState) -> None:
    if not app.config.site_dir:
        app.git.status = GitStatus(error="No site directory configured")
        return
    site = Path(app.config.site_dir).expanduser()
    app.git.status = git_status(site)
    app.git.has_refreshed = True


def render_git(win: curses.window, app: AppState) -> None:
    """Render the Git screen showing repo status for the site directory."""
    h, w = win.getmaxyx()
    gs = app.git

    if not gs.has_refreshed:
        _refresh_git_status(app)

    draw_header_bar(win, "Git", "Enter to open actions, r to refresh")

    y = 4
    st = gs.status

    if not st.is_repo:
        draw_eyebrow(win, y, 2, "Repository", w - 4)
        y += 1
        safe_addnstr(win, y, 2, "No git repository found", w - 4, curses.A_BOLD)
        y += 1
        safe_addnstr(win, y, 2, app.config.site_dir, w - 4, color(C_DIM))
        y += 2
        if st.error and st.error != "Not a git repository":
            safe_addnstr(win, y, 2, st.error, w - 4, color(C_FAIL))
            y += 2
        safe_addnstr(win, y, 2, "Press Enter to initialize a git repository.", w - 4, color(C_ACCENT))
        return

    # ── Branch & tracking ──
    draw_eyebrow(win, y, 2, "Repository", w - 4)
    y += 1
    safe_addnstr(win, y, 2, f"Branch: {st.branch or '(detached)'}", w - 4, curses.A_BOLD)
    y += 1
    if st.has_remote:
        tracking = []
        if st.ahead:
            tracking.append(f"{st.ahead} ahead")
        if st.behind:
            tracking.append(f"{st.behind} behind")
        label = ", ".join(tracking) if tracking else "up to date"
        clr = color(C_OK) if not tracking else color(C_WARN)
        safe_addnstr(win, y, 2, f"Remote: {label}", w - 4, clr)
    else:
        safe_addnstr(win, y, 2, "Remote: no upstream configured", w - 4, color(C_DIM))
    y += 2

    # ── Summary tiles ──
    tile_w = max(14, min(18, (w - 8) // 4))
    tiles = [
        ("\u2714", str(len(st.staged)), "Staged",
         color(C_OK) if st.staged else color(C_DIM)),
        ("\u270e", str(len(st.unstaged)), "Modified",
         color(C_WARN) if st.unstaged else color(C_DIM)),
        ("?", str(len(st.untracked)), "Untracked",
         color(C_INFO) if st.untracked else color(C_DIM)),
        ("\u2191", str(st.ahead), "Ahead",
         color(C_ACCENT) if st.ahead else color(C_DIM)),
    ]
    for i, (icon, val, label, clr) in enumerate(tiles):
        tx = 2 + i * (tile_w + 1)
        if tx + tile_w > w:
            break
        draw_tile(win, y, tx, tile_w, icon, val, label, clr)
    y += 6

    # ── File lists ──
    max_files = 6
    if st.staged and y < h - 3:
        y = draw_section(win, y, 2, w - 4, "Staged", trailing=str(len(st.staged)))
        y += 1
        for f in st.staged[:max_files]:
            if y >= h - 3:
                break
            safe_addnstr(win, y, 4, f, w - 6, color(C_OK))
            y += 1
        if len(st.staged) > max_files:
            safe_addnstr(win, y, 4, f"... and {len(st.staged) - max_files} more", w - 6, color(C_DIM))
            y += 1
        y += 1

    if st.unstaged and y < h - 3:
        y = draw_section(win, y, 2, w - 4, "Modified", trailing=str(len(st.unstaged)))
        y += 1
        for f in st.unstaged[:max_files]:
            if y >= h - 3:
                break
            safe_addnstr(win, y, 4, f, w - 6, color(C_WARN))
            y += 1
        if len(st.unstaged) > max_files:
            safe_addnstr(win, y, 4, f"... and {len(st.unstaged) - max_files} more", w - 6, color(C_DIM))
            y += 1
        y += 1

    if st.untracked and y < h - 3:
        y = draw_section(win, y, 2, w - 4, "Untracked", trailing=str(len(st.untracked)))
        y += 1
        for f in st.untracked[:max_files]:
            if y >= h - 3:
                break
            safe_addnstr(win, y, 4, f"? {f}", w - 6, color(C_INFO))
            y += 1
        if len(st.untracked) > max_files:
            safe_addnstr(win, y, 4, f"... and {len(st.untracked) - max_files} more", w - 6, color(C_DIM))
            y += 1


def activate_git(stdscr: curses.window, app: AppState) -> bool:
    """Interactive Git controls. Returns True if a command was run."""
    site_dir_str = app.config.site_dir
    if not site_dir_str:
        app.status = "No site directory configured"
        return False

    site = Path(site_dir_str).expanduser()
    _refresh_git_status(app)
    st = app.git.status

    if not st.is_repo:
        if confirm(stdscr, "Initialize Repository",
                   f"No git repository found in:\n{site}\n\nInitialize one now?"):
            ok, msg = git_init(site)
            if ok:
                _refresh_git_status(app)
                app.status = "Git repository initialized"
            else:
                app.status = f"Init failed: {msg}"
                return False
        else:
            app.status = "Git init cancelled"
            return False

    actions = [
        ("Refresh status", "refresh"),
        ("View diff summary", "diff"),
        ("Stage all changes", "stage"),
        ("Commit", "commit"),
        ("Push", "push"),
    ]
    selected = 0

    while True:
        _refresh_git_status(app)
        st = app.git.status
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        draw_header_bar(stdscr, "Git \u2014 Actions", "Enter runs, Esc returns")

        # Summary
        y = 4
        safe_addnstr(stdscr, y, 2, f"Branch: {st.branch or '(detached)'}", w - 4, curses.A_BOLD)
        y += 1
        parts = []
        if st.staged:
            parts.append(f"{len(st.staged)} staged")
        if st.unstaged:
            parts.append(f"{len(st.unstaged)} modified")
        if st.untracked:
            parts.append(f"{len(st.untracked)} untracked")
        if st.ahead:
            parts.append(f"{st.ahead} ahead")
        summary = " \u00b7 ".join(parts) if parts else "clean"
        safe_addnstr(stdscr, y, 2, summary, w - 4, color(C_DIM))
        y += 1

        safe_hline(stdscr, y + 1, 2, curses.ACS_HLINE, w - 4)
        y += 3

        for idx, (label, _) in enumerate(actions):
            if y + idx >= h - 2:
                break
            attr = curses.A_REVERSE | curses.A_BOLD if idx == selected else 0
            # Add contextual hints
            hint = ""
            if label == "Stage all changes" and not st.unstaged and not st.untracked:
                hint = " (nothing to stage)"
            elif label == "Commit" and not st.staged:
                hint = " (nothing staged)"
            elif label == "Push" and not st.ahead and not st.staged:
                hint = " (nothing to push)"
            safe_addnstr(stdscr, y + idx, 4, label + hint, w - 8, attr)

        safe_addnstr(stdscr, h - 1, 0, app.status.ljust(w), w, color(C_HEADER))
        stdscr.refresh()

        key = read_key(stdscr)
        if key == curses.KEY_UP:
            selected = (selected - 1) % len(actions)
        elif key == curses.KEY_DOWN:
            selected = (selected + 1) % len(actions)
        elif key in ("\x1b", "q"):
            return False
        elif key in ("\n", "\r"):
            _, action = actions[selected]
            if action == "refresh":
                _refresh_git_status(app)
                app.status = "Git status refreshed"
            elif action == "diff":
                diff = git_diff_summary(site)
                app.command_title = "Git Diff Summary"
                app.command_output = diff
                app.scroll = 0
                return True
            elif action == "stage":
                ok, msg = git_stage_all(site)
                app.status = msg
                if ok:
                    _refresh_git_status(app)
            elif action == "commit":
                msg = prompt_text(stdscr, "Commit Message", app.git.commit_message)
                if msg:
                    app.git.commit_message = msg
                    # Auto-stage if there are unstaged/untracked changes and nothing staged
                    if not st.staged and (st.unstaged or st.untracked):
                        if confirm(stdscr, "Stage Changes",
                                   "Nothing is staged. Stage all changes before committing?"):
                            ok, stage_msg = git_stage_all(site)
                            if not ok:
                                app.status = f"Stage failed: {stage_msg}"
                                continue
                        else:
                            app.status = "Commit cancelled"
                            continue
                    ok, result = git_commit(site, msg)
                    if ok:
                        app.command_title = "Git Commit"
                        app.command_output = result
                        app.scroll = 0
                        app.git.commit_message = ""
                        _refresh_git_status(app)
                        app.status = "Commit successful"
                        return True
                    else:
                        app.status = f"Commit failed: {result}"
                else:
                    app.status = "Commit cancelled"
            elif action == "push":
                if not st.has_remote:
                    app.status = "No upstream remote configured"
                    continue
                if confirm(stdscr, "Push", f"Push {st.ahead} commit(s) to remote?"):
                    ok, result = git_push(site)
                    app.command_title = "Git Push"
                    app.command_output = result
                    app.scroll = 0
                    _refresh_git_status(app)
                    app.status = "Push successful" if ok else f"Push failed"
                    return True
                else:
                    app.status = "Push cancelled"


# ─── Screen: Doctor ─────────────────────────────────────────────


def render_doctor(win: curses.window, app: AppState) -> None:
    """Render the Doctor screen."""
    h, w = win.getmaxyx()
    ds = app.doctor

    draw_header_bar(win, "Doctor", "Enter to run, Space toggles, Esc returns")

    y = 4
    # ── Header ──
    draw_eyebrow(win, y, 2, "Health", w - 4)
    y += 1
    safe_addnstr(win, y, 2, "Doctor", w - 4, curses.A_BOLD)
    y += 1
    safe_addnstr(win, y, 2, "Lint your site repo and surface misconfigurations.", w - 4, color(C_DIM))
    y += 2

    # ── Controls ──
    draw_checkbox(win, y, 2, "Site only", ds.site_only, False, w - 4)
    draw_checkbox(win, y, 22, "Host only", ds.host_only, False, w - 24)
    draw_checkbox(win, y, 42, "Config only", ds.config_only, False, w - 44)
    y += 1
    vmid_label = f"Container: {ds.target_vmid or 'All'}"
    safe_addnstr(win, y, 2, vmid_label, w - 4, color(C_DIM))
    y += 2

    # ── Status badge ──
    if not ds.has_run:
        draw_status_badge(win, y, 2, "Ready \u2014 press Enter to run", color(C_DIM), w - 4)
        y += 2
    elif ds.sections:
        # Summary metrics
        all_entries = [e for s in ds.sections for e in s.entries]
        counts = {"OK": 0, "INFO": 0, "WARN": 0, "FAIL": 0}
        for e in all_entries:
            if e.level in counts:
                counts[e.level] += 1

        tile_w = max(12, min(16, (w - 8) // 4))
        metrics = [
            ("\u2714", str(counts["OK"]), "Passed", color(C_OK)),
            ("\u2139", str(counts["INFO"]), "Info", color(C_INFO)),
            ("\u26a0", str(counts["WARN"]), "Warnings", color(C_WARN)),
            ("\u2718", str(counts["FAIL"]), "Failures", color(C_FAIL)),
        ]
        for i, (icon, val, label, clr) in enumerate(metrics):
            tx = 2 + i * (tile_w + 1)
            if tx + tile_w > w:
                break
            draw_tile(win, y, tx, tile_w, icon, val, label, clr)
        y += 6

        # ── Results ──
        for section in ds.sections:
            if y >= h - 2:
                break
            safe_addnstr(win, y, 2, f"[{section.name}]", w - 4, color(C_ACCENT, curses.A_BOLD))
            count_str = str(len(section.entries))
            safe_addnstr(win, y, w - len(count_str) - 3, count_str, len(count_str), color(C_DIM))
            y += 1
            for entry in section.entries:
                if y >= h - 2:
                    break
                if ds.filter_level and entry.level != ds.filter_level:
                    continue
                icon = level_icon(entry.level)
                clr = level_color(entry.level)
                safe_addnstr(win, y, 4, f"{icon} {entry.level:4s}", 7, clr | curses.A_BOLD)
                safe_addnstr(win, y, 12, entry.message, w - 14)
                y += 1
            y += 1
    else:
        draw_status_badge(win, y, 2, "No issues found", color(C_OK), w - 4)
        y += 2


def activate_doctor(stdscr: curses.window, app: AppState) -> bool:
    """Interactive Doctor controls. Returns True if a command was run."""
    ds = app.doctor
    controls = [
        ("Site only", "site_only"),
        ("Host only", "host_only"),
        ("Config only", "config_only"),
        ("Container VMID", "vmid"),
        ("Run Doctor", "run"),
    ]
    selected = 4  # default to Run

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        draw_header_bar(stdscr, "Doctor \u2014 Configure", "Space toggles, Enter runs, Esc returns")

        y = 4
        for idx, (label, key) in enumerate(controls):
            if key in ("site_only", "host_only", "config_only"):
                checked = getattr(ds, key)
                draw_checkbox(stdscr, y, 4, label, checked, idx == selected, w - 8)
            elif key == "vmid":
                val = ds.target_vmid or "All"
                attr = curses.A_REVERSE if idx == selected else 0
                safe_addnstr(stdscr, y, 4, f"Container: {val}", w - 8, attr)
            elif key == "run":
                attr = color(C_ACCENT, curses.A_BOLD)
                if idx == selected:
                    attr |= curses.A_REVERSE
                safe_addnstr(stdscr, y, 4, "\u25b6 Run Doctor", w - 8, attr)
            y += 1

        safe_addnstr(stdscr, h - 1, 0, app.status.ljust(w), w, color(C_HEADER))
        stdscr.refresh()

        key = read_key(stdscr)
        if key == curses.KEY_UP:
            selected = (selected - 1) % len(controls)
        elif key == curses.KEY_DOWN:
            selected = (selected + 1) % len(controls)
        elif key in ("\x1b", "q"):
            return False
        elif key in (" ",):
            _, action = controls[selected]
            if action == "site_only":
                ds.site_only = not ds.site_only
                if ds.site_only:
                    ds.host_only = False
            elif action == "host_only":
                ds.host_only = not ds.host_only
                if ds.host_only:
                    ds.site_only = False
            elif action == "config_only":
                ds.config_only = not ds.config_only
        elif key in ("\n", "\r"):
            _, action = controls[selected]
            if action == "vmid":
                value = prompt_text(stdscr, "Container VMID (blank for all)", ds.target_vmid)
                if value is not None:
                    ds.target_vmid = value
            elif action == "run":
                _run_doctor(stdscr, app)
                return True
            elif action in ("site_only", "host_only", "config_only"):
                # Enter also toggles checkboxes
                if action == "site_only":
                    ds.site_only = not ds.site_only
                    if ds.site_only:
                        ds.host_only = False
                elif action == "host_only":
                    ds.host_only = not ds.host_only
                    if ds.host_only:
                        ds.site_only = False
                elif action == "config_only":
                    ds.config_only = not ds.config_only


def _run_doctor(stdscr: curses.window, app: AppState) -> None:
    ds = app.doctor
    args: list[str] = []
    if ds.site_only:
        args.append("--site-only")
    if ds.host_only:
        args.append("--host-only")
    if ds.config_only:
        args.append("--config-only")
    if ds.target_vmid:
        args.extend(["--vmid", ds.target_vmid])

    run_command(stdscr, app, "Doctor", "proxnix-doctor", args)
    ds.sections = _parse_doctor_output(app.command_output)
    ds.has_run = True
    ds.filter_level = ""


def _parse_doctor_output(raw: str) -> list[DoctorSection]:
    cleaned = strip_ansi(raw)
    sections: list[DoctorSection] = []
    current_name = "general"
    current_entries: list[DoctorEntry] = []

    for line in cleaned.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("[") and trimmed.endswith("]"):
            if current_entries:
                sections.append(DoctorSection(current_name, current_entries))
                current_entries = []
            current_name = trimmed[1:-1]
            continue

        for level in ("OK", "INFO", "WARN", "FAIL"):
            if trimmed.startswith(level):
                message = trimmed[len(level):].strip()
                if message:
                    current_entries.append(DoctorEntry(level, message))
                break

    if current_entries:
        sections.append(DoctorSection(current_name, current_entries))

    return sections


# ─── Screen: Publish ────────────────────────────────────────────


def render_publish(win: curses.window, app: AppState) -> None:
    """Render the Publish screen."""
    h, w = win.getmaxyx()
    draw_header_bar(win, "Publish All", "Enter to configure and run")

    y = 4
    draw_eyebrow(win, y, 2, "Deploy", w - 4)
    y += 1
    safe_addnstr(win, y, 2, "Publish the full site", w - 4, curses.A_BOLD)
    y += 1
    safe_addnstr(win, y, 2, "Run a full publish across configured hosts.", w - 4, color(C_DIM))
    y += 2

    # ── Options card ──
    y = draw_section(win, y, 2, w - 4, "Options")
    y += 1
    draw_checkbox(win, y, 4, "Dry run", app.publish.dry_run, False, w - 8)
    y += 1
    draw_checkbox(win, y, 4, "Config only", app.publish.config_only, False, w - 8)
    y += 1
    draw_checkbox(win, y, 4, "Report changes", app.publish.report_changes, False, w - 8)
    y += 2

    # ── Targets card ──
    y = draw_section(win, y, 2, w - 4, "Targets")
    y += 1
    if app.config.hosts:
        safe_addnstr(win, y, 4, f"{len(app.config.hosts)} host(s):", w - 8, color(C_DIM))
        y += 1
        for host in app.config.hosts:
            if y >= h - 4:
                break
            safe_addnstr(win, y, 6, host, w - 10)
            y += 1
    else:
        safe_addnstr(win, y, 4, "\u26a0 No hosts configured", w - 8, color(C_WARN))
        y += 1
        safe_addnstr(win, y, 4, "Set SSH Hosts in Settings.", w - 8, color(C_DIM))
        y += 1

    if app.publish.vmid:
        y += 1
        safe_addnstr(win, y, 4, f"Target VMID: {app.publish.vmid}", w - 8)
    if app.publish.host_override:
        y += 1
        safe_addnstr(win, y, 4, f"Host override: {app.publish.host_override}", w - 8)


def activate_publish(stdscr: curses.window, app: AppState) -> bool:
    """Interactive Publish controls. Returns True if a command was run."""
    items = [
        ("Dry run", "dry_run"),
        ("Config only", "config_only"),
        ("Report changes", "report_changes"),
        ("Target VMID", "vmid"),
        ("Host override", "host_override"),
        ("\u25b6 Publish All", "run"),
    ]
    selected = 5  # default to Run

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        draw_header_bar(stdscr, "Publish \u2014 Configure", "Space toggles, Enter runs, Esc returns")

        y = 4
        for idx, (label, key) in enumerate(items):
            if key in ("dry_run", "config_only", "report_changes"):
                checked = getattr(app.publish, key)
                draw_checkbox(stdscr, y, 4, label, checked, idx == selected, w - 8)
            elif key == "vmid":
                val = app.publish.vmid or "all"
                attr = curses.A_REVERSE if idx == selected else 0
                safe_addnstr(stdscr, y, 4, f"Target VMID: {val}", w - 8, attr)
            elif key == "host_override":
                val = app.publish.host_override or "configured hosts"
                attr = curses.A_REVERSE if idx == selected else 0
                safe_addnstr(stdscr, y, 4, f"Host override: {val}", w - 8, attr)
            elif key == "run":
                attr = color(C_ACCENT, curses.A_BOLD)
                if idx == selected:
                    attr |= curses.A_REVERSE
                safe_addnstr(stdscr, y, 4, label, w - 8, attr)
            y += 1

        # Targets summary
        y += 1
        safe_addnstr(stdscr, y, 4, "Configured hosts:", w - 8, color(C_DIM))
        y += 1
        if app.config.hosts:
            for host in app.config.hosts[:min(5, h - y - 2)]:
                safe_addnstr(stdscr, y, 6, host, w - 10)
                y += 1
        else:
            safe_addnstr(stdscr, y, 6, "(none)", w - 10, color(C_WARN))

        safe_addnstr(stdscr, h - 1, 0, app.status.ljust(w), w, color(C_HEADER))
        stdscr.refresh()

        key = read_key(stdscr)
        if key == curses.KEY_UP:
            selected = (selected - 1) % len(items)
        elif key == curses.KEY_DOWN:
            selected = (selected + 1) % len(items)
        elif key in ("\x1b", "q"):
            return False
        elif key in (" ",):
            _, action = items[selected]
            if action == "dry_run":
                app.publish.dry_run = not app.publish.dry_run
            elif action == "config_only":
                app.publish.config_only = not app.publish.config_only
            elif action == "report_changes":
                app.publish.report_changes = not app.publish.report_changes
        elif key in ("\n", "\r"):
            _, action = items[selected]
            if action == "dry_run":
                app.publish.dry_run = not app.publish.dry_run
            elif action == "config_only":
                app.publish.config_only = not app.publish.config_only
            elif action == "report_changes":
                app.publish.report_changes = not app.publish.report_changes
            elif action == "vmid":
                value = prompt_text(stdscr, "Publish Target VMID", app.publish.vmid)
                if value is not None:
                    app.publish.vmid = value
            elif action == "host_override":
                value = prompt_text(stdscr, "Publish Host Override", app.publish.host_override)
                if value is not None:
                    app.publish.host_override = value
            elif action == "run":
                _run_publish(stdscr, app)
                return True
        elif key in ("p", "P"):
            _run_publish(stdscr, app)
            return True


def _run_publish(stdscr: curses.window, app: AppState) -> None:
    args: list[str] = []
    if app.publish.dry_run:
        args.append("--dry-run")
    if app.publish.config_only:
        args.append("--config-only")
    if app.publish.report_changes:
        args.append("--report-changes")
    if app.publish.vmid:
        args.extend(["--vmid", app.publish.vmid])
    if app.publish.host_override:
        args.extend(app.publish.host_override.split())
    run_command(stdscr, app, "Publish", "proxnix-publish", args)


# ─── Screen: Secrets ────────────────────────────────────────────


def render_secrets(win: curses.window, app: AppState) -> None:
    """Render the Secrets screen."""
    h, w = win.getmaxyx()
    ss = app.secrets
    draw_header_bar(win, "Secrets", "Enter to open actions")

    y = 4
    # Mode toggle
    modes = ["Groups", "Containers"]
    mode_x = 2
    for m in modes:
        is_active = (m.lower() == ss.mode)
        attr = color(C_ACCENT, curses.A_BOLD) if is_active else color(C_DIM)
        label = f"[{m}]" if is_active else f" {m} "
        safe_addnstr(win, y, mode_x, label, len(label), attr)
        mode_x += len(label) + 2
    safe_addnstr(win, y, mode_x, "(Tab to switch)", w - mode_x - 2, color(C_DIM))
    y += 2

    if ss.mode == "groups":
        safe_addnstr(win, y, 2, f"Scope: {ss.group or 'Shared'}", w - 4)
    else:
        safe_addnstr(win, y, 2, f"Container: {ss.vmid or 'not set'}", w - 4)
    y += 1
    safe_addnstr(win, y, 2, f"Secret name: {ss.name or 'unset'}", w - 4, color(C_DIM))
    y += 2

    # Action summary
    y = draw_section(win, y, 2, w - 4, "Actions")
    y += 1
    if ss.mode == "groups":
        actions_list = ["List shared", "List group", "Get shared", "Get group",
                        "Set shared", "Set group", "Init shared", "Rotate shared", "Rotate group"]
    else:
        actions_list = ["List container", "Get container", "Set container",
                        "Init container", "Rotate container"]
    for action in actions_list:
        if y >= h - 2:
            break
        safe_addnstr(win, y, 4, f"\u2022 {action}", w - 8)
        y += 1


def activate_secrets(stdscr: curses.window, app: AppState) -> bool:
    """Interactive Secrets controls. Returns True if a command was run."""
    ss = app.secrets

    while True:
        if ss.mode == "groups":
            actions = [
                "List shared", "List group",
                "Get shared", "Get group",
                "Set shared", "Set group",
                "Remove shared", "Remove group",
                "Rotate shared", "Rotate group",
                "Init shared",
            ]
        else:
            actions = [
                "List container",
                "Get container",
                "Set container",
                "Remove container",
                "Rotate container",
                "Init container",
            ]

        selected = 0
        while True:
            stdscr.clear()
            h, w = stdscr.getmaxyx()
            draw_header_bar(stdscr, "Secrets \u2014 Actions",
                            "Tab switches mode, Enter runs, Esc returns")

            y = 4
            # Mode toggle
            modes_display = ["Groups", "Containers"]
            mx = 2
            for m in modes_display:
                is_active = (m.lower() == ss.mode)
                attr = color(C_ACCENT, curses.A_BOLD | curses.A_REVERSE) if is_active else color(C_DIM)
                label = f" {m} "
                safe_addnstr(stdscr, y, mx, label, len(label), attr)
                mx += len(label) + 1
            y += 2

            # Context
            if ss.mode == "groups":
                safe_addnstr(stdscr, y, 4, f"Scope: {ss.group or 'Shared'}", w - 8)
            else:
                safe_addnstr(stdscr, y, 4, f"VMID: {ss.vmid or 'unset'}", w - 8)
            y += 1
            safe_addnstr(stdscr, y, 4, f"Secret: {ss.name or 'unset'}", w - 8, color(C_DIM))
            y += 1
            safe_addnstr(stdscr, y, 4, "c=set VMID  n=set name  g=set group", w - 8, color(C_DIM))
            y += 2

            safe_hline(stdscr, y, 2, curses.ACS_HLINE, w - 4)
            y += 1

            for idx, action in enumerate(actions):
                ay = y + idx
                if ay >= h - 2:
                    break
                attr = curses.A_REVERSE | curses.A_BOLD if idx == selected else 0
                safe_addnstr(stdscr, ay, 4, action, w - 8, attr)

            safe_addnstr(stdscr, h - 1, 0, app.status.ljust(w), w, color(C_HEADER))
            stdscr.refresh()

            key = read_key(stdscr)
            if key == curses.KEY_UP:
                selected = (selected - 1) % len(actions)
            elif key == curses.KEY_DOWN:
                selected = (selected + 1) % len(actions)
            elif key in ("\x1b", "q"):
                return False
            elif key == "\t":
                ss.mode = "containers" if ss.mode == "groups" else "groups"
                break  # rebuild action list
            elif key in ("c", "C"):
                value = prompt_text(stdscr, "Secret VMID", ss.vmid)
                if value is not None:
                    ss.vmid = value
            elif key in ("n", "N"):
                value = prompt_text(stdscr, "Secret Name", ss.name)
                if value is not None:
                    ss.name = value
            elif key in ("g", "G"):
                value = prompt_text(stdscr, "Secret Group", ss.group)
                if value is not None:
                    ss.group = value
            elif key in ("\n", "\r"):
                if _perform_secret_action(stdscr, app, actions[selected]):
                    return True
        else:
            continue
        continue


def _perform_secret_action(stdscr: curses.window, app: AppState, action: str) -> bool:
    ss = app.secrets
    vmid = ss.vmid
    name = ss.name
    group = ss.group

    def need_value(label: str, current: str, secret: bool = False) -> str | None:
        value = current or prompt_text(stdscr, label, current, secret=secret)
        if value is not None:
            return value.strip()
        return None

    if action == "List shared":
        run_command(stdscr, app, action, "proxnix-secrets", ["ls-shared"])
        return True
    if action == "List group":
        group = need_value("Secret Group", group)
        if not group:
            app.status = "Cancelled"
            return False
        ss.group = group
        run_command(stdscr, app, action, "proxnix-secrets", ["ls-group", group])
        return True
    if action == "List container":
        vmid = need_value("Container VMID", vmid)
        if not vmid:
            app.status = "Cancelled"
            return False
        ss.vmid = vmid
        run_command(stdscr, app, action, "proxnix-secrets", ["ls", vmid])
        return True
    if action == "Get shared":
        name = need_value("Shared Secret Name", name)
        if not name:
            app.status = "Cancelled"
            return False
        ss.name = name
        run_command(stdscr, app, action, "proxnix-secrets", ["get-shared", name])
        return True
    if action == "Get group":
        group = need_value("Secret Group", group)
        name = need_value("Secret Name", name)
        if not group or not name:
            app.status = "Cancelled"
            return False
        ss.group = group
        ss.name = name
        run_command(stdscr, app, action, "proxnix-secrets", ["get-group", group, name])
        return True
    if action == "Get container":
        vmid = need_value("Container VMID", vmid)
        name = need_value("Secret Name", name)
        if not vmid or not name:
            app.status = "Cancelled"
            return False
        ss.vmid = vmid
        ss.name = name
        run_command(stdscr, app, action, "proxnix-secrets", ["get", vmid, name])
        return True
    if action in ("Set shared", "Set group", "Set container"):
        if action == "Set container":
            vmid = need_value("Container VMID", vmid)
            name = need_value("Secret Name", name)
            if not vmid or not name:
                app.status = "Cancelled"
                return False
            ss.vmid = vmid
        elif action == "Set shared":
            name = need_value("Shared Secret Name", name)
            if not name:
                app.status = "Cancelled"
                return False
        else:
            group = need_value("Secret Group", group)
            name = need_value("Secret Name", name)
            if not group or not name:
                app.status = "Cancelled"
                return False
            ss.group = group
        ss.name = name or ss.name
        value = prompt_text(stdscr, f"{action} Value", "", secret=True)
        if value is None or not value:
            app.status = "Cancelled"
            return False
        if action == "Set container":
            run_command(stdscr, app, action, "proxnix-secrets", ["set", vmid, name], stdin_text=value)
        elif action == "Set shared":
            run_command(stdscr, app, action, "proxnix-secrets", ["set-shared", name], stdin_text=value)
        else:
            run_command(stdscr, app, action, "proxnix-secrets", ["set-group", group, name], stdin_text=value)
        return True
    if action in ("Remove shared", "Remove group", "Remove container"):
        if action == "Remove container":
            vmid = need_value("Container VMID", vmid)
            name = need_value("Secret Name", name)
            if not vmid or not name:
                app.status = "Cancelled"
                return False
            prompt = f"Remove secret {name} from container {vmid}?"
            cmd_args = ["rm", vmid, name]
            ss.vmid = vmid
            ss.name = name
        elif action == "Remove shared":
            name = need_value("Shared Secret Name", name)
            if not name:
                app.status = "Cancelled"
                return False
            prompt = f"Remove shared secret {name}?"
            cmd_args = ["rm-shared", name]
            ss.name = name
        else:
            group = need_value("Secret Group", group)
            name = need_value("Secret Name", name)
            if not group or not name:
                app.status = "Cancelled"
                return False
            prompt = f"Remove group secret {name} from {group}?"
            cmd_args = ["rm-group", group, name]
            ss.group = group
            ss.name = name
        if confirm(stdscr, action, prompt):
            run_command(stdscr, app, action, "proxnix-secrets", cmd_args)
            return True
        app.status = "Cancelled"
        return False
    if action in ("Rotate shared", "Rotate group", "Rotate container"):
        if action == "Rotate container":
            vmid = need_value("Container VMID", vmid)
            if not vmid:
                app.status = "Cancelled"
                return False
            ss.vmid = vmid
            cmd_args = ["rotate", vmid]
        elif action == "Rotate shared":
            cmd_args = ["rotate-shared"]
        else:
            group = need_value("Secret Group", group)
            if not group:
                app.status = "Cancelled"
                return False
            ss.group = group
            cmd_args = ["rotate-group", group]
        run_command(stdscr, app, action, "proxnix-secrets", cmd_args)
        return True
    if action == "Init shared":
        run_command(stdscr, app, action, "proxnix-secrets", ["init-shared"])
        return True
    if action == "Init container":
        vmid = need_value("Container VMID", vmid)
        if not vmid:
            app.status = "Cancelled"
            return False
        ss.vmid = vmid
        run_command(stdscr, app, action, "proxnix-secrets", ["init-container", vmid])
        return True
    return False


# ─── Screen: Settings ───────────────────────────────────────────


def render_settings(win: curses.window, app: AppState) -> None:
    """Render the Settings screen."""
    h, w = win.getmaxyx()
    draw_header_bar(win, "Settings", "Enter to edit fields")

    y = 4
    fields = _settings_fields(app)
    for section, entries in fields:
        if y >= h - 2:
            break
        draw_eyebrow(win, y, 2, section, w - 4)
        y += 1
        for label, value in entries:
            if y >= h - 2:
                break
            safe_addnstr(win, y, 4, f"{label}:", 20, curses.A_BOLD)
            display = value or "(not set)"
            clr = 0 if value else color(C_DIM)
            safe_addnstr(win, y, 26, display, w - 28, clr)
            y += 1
        y += 1

    safe_addnstr(win, y, 2, f"Config file: {CONFIG_FILE}", w - 4, color(C_DIM))


def _settings_fields(app: AppState) -> list[tuple[str, list[tuple[str, str]]]]:
    c = app.config
    return [
        ("Site Repo", [
            ("Site directory", c.site_dir),
        ]),
        ("Hosts", [
            ("SSH hosts", " ".join(c.hosts) if c.hosts else ""),
            ("SSH identity", c.ssh_identity),
        ]),
        ("Remote Paths", [
            ("Remote dir", c.remote_dir),
            ("Remote priv dir", c.remote_priv_dir),
            ("Relay identity", c.remote_host_relay_identity),
        ]),
    ]


def activate_settings(stdscr: curses.window, app: AppState) -> None:
    """Interactive Settings editor."""
    editable = [
        ("Site directory", "site_dir"),
        ("SSH hosts", "hosts"),
        ("SSH identity", "ssh_identity"),
        ("Remote dir", "remote_dir"),
        ("Remote priv dir", "remote_priv_dir"),
        ("Relay identity", "remote_host_relay_identity"),
        ("\u25b6 Save to config file", "save"),
    ]
    selected = 0
    dirty = False

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        draw_header_bar(stdscr, "Settings \u2014 Edit",
                        "Enter to edit, s to save, Esc returns")

        y = 4
        for idx, (label, key) in enumerate(editable):
            if y >= h - 2:
                break
            attr = curses.A_REVERSE if idx == selected else 0
            if key == "save":
                c = color(C_ACCENT, curses.A_BOLD) | attr
                safe_addnstr(stdscr, y, 4, label, w - 8, c)
            elif key == "hosts":
                val = " ".join(app.config.hosts) if app.config.hosts else ""
                safe_addnstr(stdscr, y, 4, f"{label}: {val or '(not set)'}", w - 8, attr)
            else:
                val = getattr(app.config, key, "")
                safe_addnstr(stdscr, y, 4, f"{label}: {val or '(not set)'}", w - 8, attr)
            y += 1

        y += 1
        if dirty:
            safe_addnstr(stdscr, y, 4, "\u25cf Unsaved changes", w - 8, color(C_WARN, curses.A_BOLD))
        safe_addnstr(stdscr, y + 1, 4, f"Config: {CONFIG_FILE}", w - 8, color(C_DIM))

        safe_addnstr(stdscr, h - 1, 0, app.status.ljust(w), w, color(C_HEADER))
        stdscr.refresh()

        key = read_key(stdscr)
        if key == curses.KEY_UP:
            selected = (selected - 1) % len(editable)
        elif key == curses.KEY_DOWN:
            selected = (selected + 1) % len(editable)
        elif key in ("\x1b", "q"):
            if dirty:
                if confirm(stdscr, "Unsaved Changes", "Discard unsaved settings changes?"):
                    app.refresh()
                    return
            else:
                return
        elif key in ("s", "S"):
            _save_config(stdscr, app)
            dirty = False
        elif key in ("\n", "\r"):
            _, field_key = editable[selected]
            if field_key == "save":
                _save_config(stdscr, app)
                dirty = False
            elif field_key == "hosts":
                val = prompt_text(stdscr, "SSH Hosts (space-separated)",
                                  " ".join(app.config.hosts))
                if val is not None:
                    app.config.hosts = val.split()
                    dirty = True
            else:
                val = prompt_text(stdscr, editable[selected][0],
                                  getattr(app.config, field_key, ""))
                if val is not None:
                    setattr(app.config, field_key, val)
                    dirty = True


def _save_config(stdscr: curses.window, app: AppState) -> None:
    """Write config back to the config file in PROXNIX_* format."""
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"PROXNIX_SITE_DIR={app.config.site_dir}",
            f"PROXNIX_HOSTS={' '.join(app.config.hosts)}",
            f"PROXNIX_SSH_IDENTITY={app.config.ssh_identity}",
            f"PROXNIX_REMOTE_DIR={app.config.remote_dir}",
            f"PROXNIX_REMOTE_PRIV_DIR={app.config.remote_priv_dir}",
            f"PROXNIX_REMOTE_HOST_RELAY_IDENTITY={app.config.remote_host_relay_identity}",
        ]
        CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        app.refresh()
        app.status = "Settings saved"
    except OSError as exc:
        app.status = f"Save failed: {exc}"


# ─── Screen: Output ─────────────────────────────────────────────


def render_output(win: curses.window, app: AppState) -> None:
    h, w = win.getmaxyx()
    title = app.command_title or "Command Output"
    draw_header_bar(win, title, "Up/Down/PgUp/PgDn scroll, Enter clears")

    # Status badge
    if app.command_output:
        lines_text = strip_ansi(app.command_output)
        line_count = len(lines_text.splitlines())
        safe_addnstr(win, 1, w - 16, f"{line_count} lines", 12, color(C_DIM))

    body = strip_ansi(app.command_output) if app.command_output else "No command has been run yet."
    lines = wrap_lines(body, w - 4)
    max_scroll = max(0, len(lines) - max(1, h - 5))
    app.scroll = max(0, min(app.scroll, max_scroll))
    visible = lines[app.scroll : app.scroll + max(1, h - 5)]
    for idx, line in enumerate(visible):
        safe_addnstr(win, 4 + idx, 2, line, w - 4)


def activate_output(stdscr: curses.window, app: AppState) -> None:
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        # Use full screen for output
        title = app.command_title or "Command Output"
        safe_addnstr(stdscr, 0, 0, f" {title} ".ljust(w), w, color(C_HEADER, curses.A_BOLD))

        # Status bar info
        if app.command_output:
            clean = strip_ansi(app.command_output)
            line_count = len(clean.splitlines())
            info = f"{line_count} lines"
            safe_addnstr(stdscr, 0, w - len(info) - 2, info, len(info), color(C_HEADER))

        safe_addnstr(stdscr, 1, 1, "Up/Down/PgUp/PgDn scroll, Enter clears, Esc returns",
                     w - 2, color(C_DIM))
        safe_hline(stdscr, 2, 0, curses.ACS_HLINE, w)

        body = strip_ansi(app.command_output) if app.command_output else "No command output."
        lines = wrap_lines(body, w - 4)
        visible_rows = max(1, h - 5)
        max_scroll = max(0, len(lines) - visible_rows)
        app.scroll = max(0, min(app.scroll, max_scroll))

        for idx, line in enumerate(lines[app.scroll : app.scroll + visible_rows]):
            safe_addnstr(stdscr, 3 + idx, 2, line, w - 4)

        # Scroll indicator
        if max_scroll > 0:
            pct = int((app.scroll / max_scroll) * 100) if max_scroll else 0
            scroll_info = f" {app.scroll + 1}-{min(app.scroll + visible_rows, len(lines))}/{len(lines)} ({pct}%) "
            safe_addnstr(stdscr, h - 1, 0, scroll_info.ljust(w), w, color(C_HEADER))
        else:
            safe_addnstr(stdscr, h - 1, 0, app.status.ljust(w), w, color(C_HEADER))

        stdscr.refresh()
        key = read_key(stdscr)
        if key in ("\x1b", "q"):
            return
        if key in ("\n", "\r"):
            app.command_output = ""
            app.command_title = ""
            app.scroll = 0
            app.status = "Cleared output"
            return
        if key == curses.KEY_UP:
            app.scroll = max(0, app.scroll - 1)
        elif key == curses.KEY_DOWN:
            app.scroll = min(max_scroll, app.scroll + 1)
        elif key == curses.KEY_NPAGE:
            app.scroll = min(max_scroll, app.scroll + max(5, visible_rows))
        elif key == curses.KEY_PPAGE:
            app.scroll = max(0, app.scroll - max(5, visible_rows))


# ─── Screen: Help ───────────────────────────────────────────────


def render_help(win: curses.window, app: AppState) -> None:
    h, w = win.getmaxyx()
    draw_header_bar(win, "Help", "Keybindings and behavior")

    sections = [
        ("Navigation", [
            "Up/Down     Navigate sidebar or lists",
            "Enter       Open / activate selected item",
            "Esc / q     Go back / quit",
            "Tab         Switch mode (in Secrets)",
            "r           Refresh config and containers",
        ]),
        ("In screens", [
            "Space       Toggle checkboxes",
            "Enter       Edit field / run action",
            "p           Quick-run publish",
            "c/n/g       Set VMID / name / group (Secrets)",
        ]),
        ("Output viewer", [
            "Up/Down     Scroll line by line",
            "PgUp/PgDn   Scroll by page",
            "Enter       Clear output",
        ]),
        ("Git", [
            "Stage / commit / push site changes",
            "Status shows staged, modified, untracked",
        ]),
        ("Commands used", [
            "proxnix-publish",
            "proxnix-secrets",
            "proxnix-doctor",
        ]),
        ("Config", [
            f"{CONFIG_FILE}",
        ]),
    ]

    y = 4
    for section_title, lines in sections:
        if y >= h - 2:
            break
        draw_eyebrow(win, y, 2, section_title, w - 4)
        y += 1
        for line in lines:
            if y >= h - 2:
                break
            safe_addnstr(win, y, 4, line, w - 6)
            y += 1
        y += 1


# ─── Main Loop ──────────────────────────────────────────────────


def run_tui(stdscr: curses.window) -> None:
    curses.curs_set(0)
    setup_theme()
    stdscr.keypad(True)

    app = AppState()
    app.refresh()

    sidebar_items = build_sidebar_items(app)
    selectable = selectable_indices(sidebar_items)
    selected_pos = 0  # index into selectable list

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        # Rebuild sidebar items (container list may have changed)
        sidebar_items = build_sidebar_items(app)
        selectable = selectable_indices(sidebar_items)
        if selected_pos >= len(selectable):
            selected_pos = max(0, len(selectable) - 1)

        selected_idx = selectable[selected_pos] if selectable else 0
        sidebar_w = draw_sidebar(stdscr, sidebar_items, selected_idx)

        # Detail pane
        pane_w = w - sidebar_w - 1
        if pane_w > 5:
            pane = stdscr.derwin(h, pane_w, 0, sidebar_w + 1)
            pane.erase()

            item = sidebar_items[selected_idx]
            if item.key == "git":
                render_git(pane, app)
            elif item.key == "doctor":
                render_doctor(pane, app)
            elif item.key == "publish":
                render_publish(pane, app)
            elif item.key == "secrets":
                render_secrets(pane, app)
            elif item.key == "settings":
                render_settings(pane, app)
            elif item.key == "help":
                render_help(pane, app)
            elif item.kind == "container":
                render_container_detail(pane, app, item.key)
            else:
                render_help(pane, app)

        # Status bar
        status = app.status or "Ready"
        safe_addnstr(stdscr, h - 1, 0, f" {status} ".ljust(w), w, color(C_HEADER))
        stdscr.refresh()

        key = read_key(stdscr)
        if key == curses.KEY_UP:
            selected_pos = (selected_pos - 1) % len(selectable)
        elif key == curses.KEY_DOWN:
            selected_pos = (selected_pos + 1) % len(selectable)
        elif key == "\t":
            selected_pos = (selected_pos + 1) % len(selectable)
        elif key in ("\n", "\r"):
            item = sidebar_items[selected_idx]
            prev_output = app.command_output
            ran_command = False

            if item.key == "git":
                ran_command = activate_git(stdscr, app)
            elif item.key == "doctor":
                ran_command = activate_doctor(stdscr, app)
            elif item.key == "publish":
                ran_command = activate_publish(stdscr, app)
            elif item.key == "secrets":
                ran_command = activate_secrets(stdscr, app)
            elif item.key == "settings":
                activate_settings(stdscr, app)
            elif item.kind == "container":
                ran_command = activate_container_detail(stdscr, app, item.key)

            # If output changed, show it (but stay on current sidebar item)
            if ran_command and app.command_output != prev_output:
                activate_output(stdscr, app)
        elif key in ("q", "Q"):
            return
        elif key in ("r", "R"):
            app.refresh()
            app.git.has_refreshed = False
            app.status = f"Refreshed: {len(app.containers)} container(s)"


def main(argv: list[str] | None = None, *, prog: str = "proxnix-tui") -> int:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--config", type=Path, help="Path to the proxnix workstation config file")
    parsed = parser.parse_args(sys.argv[1:] if argv is None else argv)

    global CONFIG_FILE
    if parsed.config is not None:
        CONFIG_FILE = parsed.config.expanduser()

    curses.wrapper(run_tui)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
