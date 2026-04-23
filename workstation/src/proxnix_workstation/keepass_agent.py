from __future__ import annotations

import base64
import hashlib
import hmac
import os
import tempfile
from pathlib import Path

from .errors import ConfigError, ProxnixWorkstationError
from .runtime import command_env, ensure_commands, run_command


_PYKEEPASS_AGENT_NAMESPACE = "proxnix-keepass-unlock@proxnix"
_PYKEEPASS_HKDF_SALT = b"proxnix-pykeepass-agent-password-v1"


def _agent_socket() -> str:
    configured = os.environ.get("PROXNIX_PYKEEPASS_AGENT_SOCKET", "").strip()
    if configured:
        return configured
    inherited = os.environ.get("SSH_AUTH_SOCK", "").strip()
    if inherited:
        return inherited
    raise ProxnixWorkstationError(
        "PROXNIX_PYKEEPASS_AGENT_SOCKET or SSH_AUTH_SOCK must be set; no SSH agent is available"
    )


def _agent_env() -> dict[str, str]:
    return command_env({"SSH_AUTH_SOCK": _agent_socket()})


def _normalize_public_key(public_key: str) -> str:
    parts = public_key.strip().split()
    if len(parts) < 2:
        raise ConfigError("PROXNIX_PYKEEPASS_AGENT_PUBLIC_KEY must be a valid SSH public key")
    key_type, key_material = parts[0], parts[1]
    if key_type != "ssh-ed25519":
        raise ConfigError(
            "PROXNIX_PYKEEPASS_AGENT_PUBLIC_KEY must reference an ssh-ed25519 public key"
        )
    return f"{key_type} {key_material}"


def _derive_password_material(signature: bytes, *, context: str) -> str:
    prk = hmac.new(_PYKEEPASS_HKDF_SALT, signature, hashlib.sha256).digest()
    okm = hmac.new(prk, context.encode("utf-8") + b"\x01", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(okm).decode("ascii").rstrip("=")


def _challenge_text(*, public_key: str, context: str) -> str:
    return "\n".join(
        [
            "proxnix pykeepass unlock",
            "version: 1",
            f"context: {context}",
            f"public-key: {public_key}",
            "",
        ]
    )


def available_pykeepass_agent_public_keys() -> list[str]:
    ensure_commands(["ssh-add"])
    completed = run_command(["ssh-add", "-L"], env=_agent_env(), check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if "The agent has no identities." in detail:
            return []
        raise ProxnixWorkstationError(f"failed to list SSH agent keys: {detail}")
    keys: list[str] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("The agent has no identities."):
            continue
        try:
            keys.append(_normalize_public_key(line))
        except ConfigError:
            continue
    return sorted(set(keys))


def pykeepass_agent_context(database_path: str) -> str:
    configured = os.environ.get("PROXNIX_PYKEEPASS_AGENT_CONTEXT", "").strip()
    if configured:
        return configured
    return Path(database_path).name


def derive_pykeepass_agent_password(database_path: str, configured_public_key: str) -> str:
    ensure_commands(["ssh-keygen", "ssh-add"])
    target_key = _normalize_public_key(configured_public_key)
    available_keys = available_pykeepass_agent_public_keys()
    if target_key not in available_keys:
        available_detail = "\n".join(available_keys)
        suffix = f"\nAvailable agent keys:\n{available_detail}" if available_detail else ""
        raise ProxnixWorkstationError(
            "configured PROXNIX_PYKEEPASS_AGENT_PUBLIC_KEY was not found in the SSH agent" + suffix
        )

    context = pykeepass_agent_context(database_path)
    challenge = _challenge_text(public_key=target_key, context=context)
    with tempfile.TemporaryDirectory(prefix="proxnix-pykeepass-agent.") as temp_dir:
        public_key_path = Path(temp_dir) / "identity.pub"
        public_key_path.write_text(target_key + "\n", encoding="utf-8")
        public_key_path.chmod(0o600)
        completed = run_command(
            [
                "ssh-keygen",
                "-Y",
                "sign",
                "-n",
                _PYKEEPASS_AGENT_NAMESPACE,
                "-f",
                str(public_key_path),
            ],
            env=_agent_env(),
            input_text=challenge,
        )
    return _derive_password_material(completed.stdout.encode("utf-8"), context=context)
