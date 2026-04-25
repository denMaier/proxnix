from __future__ import annotations

import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .config import WorkstationConfig
from .errors import ConfigError, PlanningError, ProxnixWorkstationError
from .runtime import command_env, run_command


_SUPPORTED_PRIVATE_KEY_HEADERS = {
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN DSA PRIVATE KEY-----",
}


def _validate_private_key_text(private_text: str, *, source: str) -> None:
    try:
        first_line = private_text.splitlines()[0]
    except IndexError as exc:
        raise ConfigError(
            f"PROXNIX_SOPS_MASTER_IDENTITY must point to an SSH private key usable as an age identity: {source}"
        ) from exc
    if first_line not in _SUPPORTED_PRIVATE_KEY_HEADERS:
        raise ConfigError(
            f"PROXNIX_SOPS_MASTER_IDENTITY must point to an SSH private key usable as an age identity: {source}"
        )


@contextmanager
def _master_env(config: WorkstationConfig, *, master_private_key_text: str | None = None):
    if master_private_key_text is None:
        from .provider_keys import sops_master_identity_path

        identity_path = sops_master_identity_path(config)
        if not identity_path.is_file():
            raise ConfigError(f"SOPS master SSH identity not found: {identity_path}")
        private_text = identity_path.read_text(encoding="utf-8", errors="replace")
        _validate_private_key_text(private_text, source=str(identity_path))
        env = command_env({"SOPS_AGE_SSH_PRIVATE_KEY_FILE": str(identity_path)})
        env.pop("SOPS_AGE_KEY_FILE", None)
        yield env
        return

    _validate_private_key_text(master_private_key_text, source="secret provider master identity")
    with tempfile.TemporaryDirectory(prefix="proxnix-master-identity.") as temp_dir:
        private_path = Path(temp_dir) / "identity"
        private_path.write_text(master_private_key_text, encoding="utf-8")
        private_path.chmod(0o600)
        env = command_env({"SOPS_AGE_SSH_PRIVATE_KEY_FILE": str(private_path)})
        env.pop("SOPS_AGE_KEY_FILE", None)
        yield env


def public_key_from_private_path(path: Path, *, source: str) -> str:
    completed = run_command(
        ["ssh-keygen", "-y", "-f", str(path)],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise ConfigError(
            "PROXNIX_SOPS_MASTER_IDENTITY must point to an SSH private key usable as an age identity: "
            f"{source}{suffix}"
        )
    return completed.stdout.strip()


def public_key_from_private_text(private_text: str, *, source: str) -> str:
    with tempfile.TemporaryDirectory(prefix="proxnix-identity-key.") as temp_dir:
        private_path = Path(temp_dir) / "identity"
        private_path.write_text(private_text, encoding="utf-8")
        private_path.chmod(0o600)
        return public_key_from_private_path(private_path, source=source)


def master_recipient(config: WorkstationConfig, *, master_private_key_text: str | None = None) -> str:
    if master_private_key_text is not None:
        _validate_private_key_text(master_private_key_text, source="secret provider master identity")
        return public_key_from_private_text(master_private_key_text, source="secret provider master identity")
    with _master_env(config):
        from .provider_keys import sops_master_identity_path

        identity_path = sops_master_identity_path(config)
        return public_key_from_private_path(identity_path, source=str(identity_path))


def sops_path(name: str) -> str:
    if name.startswith("["):
        return name
    return "[" + json.dumps(name) + "]"


def sops_decrypt_yaml_text(
    config: WorkstationConfig,
    store: Path,
    *,
    master_private_key_text: str | None = None,
) -> str:
    with _master_env(config, master_private_key_text=master_private_key_text) as env:
        completed = run_command(
            ["sops", "decrypt", "--input-type", "yaml", "--output-type", "yaml", str(store)],
            env=env,
        )
    return completed.stdout


def sops_decrypt_json(
    config: WorkstationConfig,
    store: Path,
    *,
    master_private_key_text: str | None = None,
) -> dict[str, object]:
    with _master_env(config, master_private_key_text=master_private_key_text) as env:
        completed = run_command(
            ["sops", "decrypt", "--input-type", "yaml", "--output-type", "json", str(store)],
            env=env,
        )
    data = json.loads(completed.stdout)
    if not isinstance(data, dict):
        raise PlanningError(f"invalid proxnix secret store payload: {store}")
    return data


def _sops_encrypt_text(
    config: WorkstationConfig,
    *,
    input_type: str,
    output_type: str,
    recipients: str,
    plaintext: str,
    master_private_key_text: str | None = None,
) -> str:
    with _master_env(config, master_private_key_text=master_private_key_text) as env:
        completed = run_command(
            [
                "sops",
                "--encrypt",
                "--age",
                recipients,
                "--input-type",
                input_type,
                "--output-type",
                output_type,
                "/dev/stdin",
            ],
            env=env,
            input_text=plaintext,
        )
    return completed.stdout


def sops_encrypt_yaml_to_file(
    config: WorkstationConfig,
    source_yaml: Path,
    recipients: str,
    destination: Path,
    *,
    master_private_key_text: str | None = None,
) -> None:
    with _master_env(config, master_private_key_text=master_private_key_text) as env:
        completed = run_command(
            [
                "sops",
                "--encrypt",
                "--age",
                recipients,
                "--input-type",
                "yaml",
                "--output-type",
                "yaml",
                str(source_yaml),
            ],
            env=env,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(completed.stdout, encoding="utf-8")
    destination.chmod(0o600)


def sops_encrypt_yaml_text(
    config: WorkstationConfig,
    plaintext: str,
    recipients: str,
    *,
    master_private_key_text: str | None = None,
) -> str:
    return _sops_encrypt_text(
        config,
        input_type="yaml",
        output_type="yaml",
        recipients=recipients,
        plaintext=plaintext,
        master_private_key_text=master_private_key_text,
    )


def sops_encrypt_json_to_file(
    config: WorkstationConfig,
    source_json: Path,
    recipients: str,
    destination: Path,
    *,
    master_private_key_text: str | None = None,
) -> None:
    with _master_env(config, master_private_key_text=master_private_key_text) as env:
        completed = run_command(
            [
                "sops",
                "--encrypt",
                "--age",
                recipients,
                "--input-type",
                "json",
                "--output-type",
                "yaml",
                str(source_json),
            ],
            env=env,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(completed.stdout, encoding="utf-8")
    destination.chmod(0o600)


def sops_encrypt_json_text(
    config: WorkstationConfig,
    plaintext: str,
    recipients: str,
    *,
    master_private_key_text: str | None = None,
) -> str:
    return _sops_encrypt_text(
        config,
        input_type="json",
        output_type="yaml",
        recipients=recipients,
        plaintext=plaintext,
        master_private_key_text=master_private_key_text,
    )


def write_identity_payload(identity_text: str) -> str:
    lines = identity_text.splitlines()
    body = "\n".join(f"  {line}" for line in lines)
    return f"identity: |\n{body}\n"


def parse_identity_payload(text: str) -> str:
    lines = text.splitlines()
    if not lines or not lines[0].strip().startswith("identity: |"):
        raise PlanningError("invalid proxnix identity payload")

    body = lines[1:]
    base_indent: int | None = None
    output_lines: list[str] = []
    for line in body:
        if line.strip() == "":
            output_lines.append("")
            continue
        indent = len(line) - len(line.lstrip(" "))
        if base_indent is None:
            if indent == 0:
                raise PlanningError("invalid proxnix identity payload")
            base_indent = indent
        if indent < base_indent:
            raise PlanningError("invalid proxnix identity payload")
        output_lines.append(line[base_indent:])
    return "\n".join(output_lines) + ("\n" if output_lines else "")


def decrypt_identity_to_file(
    config: WorkstationConfig,
    store: Path,
    destination: Path,
    *,
    master_private_key_text: str | None = None,
) -> None:
    identity_text = parse_identity_payload(
        sops_decrypt_yaml_text(config, store, master_private_key_text=master_private_key_text)
    )
    with destination.open("w", encoding="utf-8") as handle:
        handle.write(identity_text)
    destination.chmod(0o600)


def decrypt_identity_text(
    config: WorkstationConfig,
    store: Path,
    *,
    master_private_key_text: str | None = None,
) -> str:
    return parse_identity_payload(
        sops_decrypt_yaml_text(config, store, master_private_key_text=master_private_key_text)
    )


def identity_public_key_from_store(
    config: WorkstationConfig,
    store: Path,
    *,
    master_private_key_text: str | None = None,
) -> str:
    identity_text = decrypt_identity_text(config, store, master_private_key_text=master_private_key_text)
    return public_key_from_private_text(identity_text, source=str(store))


def encrypt_identity_text_to_file(
    config: WorkstationConfig,
    identity_text: str,
    recipients: str,
    destination: Path,
    *,
    master_private_key_text: str | None = None,
) -> None:
    payload = write_identity_payload(identity_text.rstrip("\n"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        sops_encrypt_yaml_text(
            config,
            payload,
            recipients,
            master_private_key_text=master_private_key_text,
        ),
        encoding="utf-8",
    )
    destination.chmod(0o600)


def reencrypt_identity_store_to_file(
    config: WorkstationConfig,
    source_store: Path,
    recipients: str,
    destination: Path,
    *,
    master_private_key_text: str | None = None,
) -> None:
    payload = sops_decrypt_yaml_text(config, source_store, master_private_key_text=master_private_key_text)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        sops_encrypt_yaml_text(
            config,
            payload,
            recipients,
            master_private_key_text=master_private_key_text,
        ),
        encoding="utf-8",
    )
    destination.chmod(0o600)


def generate_identity_keypair() -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix="proxnix-identity-generate.") as temp_dir:
        private_path = Path(temp_dir) / "identity"
        run_command(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private_path)],
            capture_output=True,
        )
        private_text = private_path.read_text(encoding="utf-8")
        public_text = private_path.with_suffix(".pub").read_text(encoding="utf-8").strip()
    return private_text, public_text


def ensure_flat_secret_map(data: dict[str, object], *, source: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in data.items():
        if key == "sops":
            continue
        if not isinstance(value, str):
            raise PlanningError(f"non-string secret values in {source}: {key}")
        result[key] = value
    return result


def read_secret_value() -> str:
    import getpass
    import sys

    if sys.stdin.isatty():
        value = getpass.getpass("Secret value: ")
        confirm = getpass.getpass("Confirm: ")
        value = value.rstrip("\r")
        confirm = confirm.rstrip("\r")
        if value != confirm:
            raise ProxnixWorkstationError("values do not match")
    else:
        value = sys.stdin.read().rstrip("\r")

    if not value:
        raise ProxnixWorkstationError("empty secret value")
    return value


def secret_value_json(value: str) -> str:
    return json.dumps(value)


def ensure_private_permissions(root: Path) -> None:
    if not root.exists():
        return
    for path in [root, *sorted(root.rglob("*"))]:
        if path.is_dir():
            path.chmod(0o700)
        elif path.is_file():
            mode = stat.S_IMODE(path.stat().st_mode)
            path.chmod(mode if mode else 0o600)
