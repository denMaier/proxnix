from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .errors import ProxnixWorkstationError


class CommandError(ProxnixWorkstationError):
    """Raised when an external command fails."""


def shell_join(args: Sequence[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def ensure_commands(names: Iterable[str]) -> None:
    for name in names:
        if shutil.which(name) is None:
            raise CommandError(f"{name} not found")


def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    check: bool = True,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(args),
            cwd=None if cwd is None else str(cwd),
            env=None if env is None else dict(env),
            input=input_text,
            text=True,
            capture_output=capture_output,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandError(f"command not found: {args[0]}") from exc

    if check and completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        suffix = f": {stderr}" if stderr else ""
        raise CommandError(f"{shell_join(args)} failed with exit code {completed.returncode}{suffix}")

    return completed


def command_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env
