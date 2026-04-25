from __future__ import annotations

import hashlib
import shlex
import tempfile
from pathlib import Path

from .config import WorkstationConfig
from .errors import ProxnixWorkstationError
from .runtime import run_command


class SSHSession:
    def __init__(self, config: WorkstationConfig, host: str, temp_root: Path | None = None) -> None:
        self.config = config
        self.host = host
        self._temp_dir_cm = None
        self._socket_dir_cm = None
        self.temp_root = temp_root
        self.control_socket: Path | None = None

    def _ssh_base_args(self) -> list[str]:
        if self.control_socket is None:
            raise ProxnixWorkstationError("SSHSession used outside context manager")
        args = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ControlPath={self.control_socket}",
        ]
        if self.config.ssh_identity is not None:
            args.extend(["-i", str(self.config.ssh_identity)])
        return args

    def _cleanup_temp_dirs(self, exc_type=None, exc=None, tb=None) -> None:
        if self._temp_dir_cm is not None:
            self._temp_dir_cm.__exit__(exc_type, exc, tb)
            self._temp_dir_cm = None
        if self._socket_dir_cm is not None:
            self._socket_dir_cm.__exit__(exc_type, exc, tb)
            self._socket_dir_cm = None

    def __enter__(self) -> "SSHSession":
        try:
            if self.temp_root is None:
                self._temp_dir_cm = tempfile.TemporaryDirectory(prefix="proxnix-ssh.", dir="/tmp")
                self.temp_root = Path(self._temp_dir_cm.__enter__())
                socket_root = self.temp_root
            else:
                self._socket_dir_cm = tempfile.TemporaryDirectory(prefix="proxnix-ssh.", dir="/tmp")
                socket_root = Path(self._socket_dir_cm.__enter__())
            host_digest = hashlib.sha256(self.host.encode("utf-8")).hexdigest()[:12]
            self.control_socket = socket_root / f"s-{host_digest}.sock"
            args = self._ssh_base_args()
            args[1:1] = ["-nNf", "-o", "ControlMaster=yes", "-o", "ControlPersist=60"]
            args.append(self.host)
            run_command(args, capture_output=True)
            return self
        except Exception:
            self.control_socket = None
            self._cleanup_temp_dirs()
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.control_socket is not None and self.control_socket.exists():
            run_command(
                [*self._ssh_base_args(), "-O", "exit", self.host],
                check=False,
            )
        self.control_socket = None
        self._cleanup_temp_dirs(exc_type, exc, tb)

    def run(self, remote_command: str, *, check: bool = True, capture_output: bool = True):
        args = self._ssh_base_args()
        args.extend(["-o", "ControlMaster=no"])
        args.extend([self.host, remote_command])
        return run_command(args, check=check, capture_output=capture_output)

    def rsync_ssh_command(self) -> str:
        args = self._ssh_base_args()
        args.extend(["-o", "ControlMaster=no"])
        return " ".join(shlex.quote(arg) for arg in args)
