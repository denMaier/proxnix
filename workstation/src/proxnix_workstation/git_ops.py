"""Git operations for the proxnix site directory."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GitStatus:
    is_repo: bool = False
    branch: str = ""
    staged: list[str] = field(default_factory=list)
    unstaged: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    ahead: int = 0
    behind: int = 0
    has_remote: bool = False
    error: str = ""


def _run_git(site_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(site_dir),
        text=True,
        capture_output=True,
    )


def git_init(site_dir: Path) -> tuple[bool, str]:
    """Initialize a new git repository in the site directory."""
    result = _run_git(site_dir, "init")
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, result.stdout.strip()


def git_status(site_dir: Path) -> GitStatus:
    """Collect git status for the site directory."""
    status = GitStatus()

    if not (site_dir / ".git").exists():
        result = _run_git(site_dir, "rev-parse", "--is-inside-work-tree")
        if result.returncode != 0:
            status.error = "Not a git repository"
            return status

    status.is_repo = True

    # Branch name
    result = _run_git(site_dir, "branch", "--show-current")
    if result.returncode == 0:
        status.branch = result.stdout.strip()

    # Porcelain status
    result = _run_git(site_dir, "status", "--porcelain=v1", "-u")
    if result.returncode != 0:
        status.error = result.stderr.strip()
        return status

    for line in result.stdout.splitlines():
        if len(line) < 3:
            continue
        index_flag = line[0]
        worktree_flag = line[1]
        path = line[3:]

        if index_flag == "?":
            status.untracked.append(path)
        else:
            if index_flag not in (" ", "?"):
                status.staged.append(f"{index_flag} {path}")
            if worktree_flag not in (" ", "?"):
                status.unstaged.append(f"{worktree_flag} {path}")

    # Ahead/behind tracking
    result = _run_git(site_dir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if result.returncode == 0:
        status.has_remote = True
        upstream = result.stdout.strip()
        result = _run_git(site_dir, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if len(parts) == 2:
                status.ahead = int(parts[0])
                status.behind = int(parts[1])

    return status


def git_diff_summary(site_dir: Path) -> str:
    """Return a combined diff summary (staged + unstaged)."""
    parts: list[str] = []

    result = _run_git(site_dir, "diff", "--cached", "--stat")
    if result.returncode == 0 and result.stdout.strip():
        parts.append("Staged changes:\n" + result.stdout.strip())

    result = _run_git(site_dir, "diff", "--stat")
    if result.returncode == 0 and result.stdout.strip():
        parts.append("Unstaged changes:\n" + result.stdout.strip())

    return "\n\n".join(parts) if parts else "No changes."


def git_stage_all(site_dir: Path) -> tuple[bool, str]:
    """Stage all changes (git add -A)."""
    result = _run_git(site_dir, "add", "-A")
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, "All changes staged."


def git_commit(site_dir: Path, message: str) -> tuple[bool, str]:
    """Create a commit with the given message."""
    if not message.strip():
        return False, "Commit message cannot be empty."
    result = _run_git(site_dir, "commit", "-m", message)
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, result.stdout.strip()


def git_push(site_dir: Path) -> tuple[bool, str]:
    """Push to the upstream remote."""
    result = _run_git(site_dir, "push")
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    output = result.stderr.strip() or result.stdout.strip()
    return True, output or "Pushed successfully."
