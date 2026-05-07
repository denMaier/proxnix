from __future__ import annotations

import json
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .config import WorkstationConfig
from .errors import ConfigError, PlanningError, ProxnixWorkstationError
from .runtime import run_command


SECRET_BUNDLE_SCHEMA = "proxnix.secrets.v1"

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
            f"PROXNIX_AGE_MASTER_IDENTITY must point to an SSH private key usable as an age identity: {source}"
        ) from exc
    if first_line not in _SUPPORTED_PRIVATE_KEY_HEADERS:
        raise ConfigError(
            f"PROXNIX_AGE_MASTER_IDENTITY must point to an SSH private key usable as an age identity: {source}"
        )


def _recipient_args(recipients: str) -> list[str]:
    args: list[str] = []
    for recipient in [item.strip() for item in recipients.split(",") if item.strip()]:
        args.extend(["-r", recipient])
    if not args:
        raise PlanningError("no age recipients configured")
    return args


@contextmanager
def _identity_file(identity_text: str):
    _validate_private_key_text(identity_text, source="secret provider identity")
    with tempfile.TemporaryDirectory(prefix="proxnix-age-identity.") as temp_dir:
        path = Path(temp_dir) / "identity"
        path.write_text(identity_text, encoding="utf-8")
        path.chmod(0o600)
        yield path


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
            "PROXNIX_AGE_MASTER_IDENTITY must point to an SSH private key usable as an age identity: "
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
    from .provider_keys import age_master_identity_path

    identity_path = age_master_identity_path(config)
    if not identity_path.is_file():
        raise ConfigError(f"age master SSH identity not found: {identity_path}")
    private_text = identity_path.read_text(encoding="utf-8", errors="replace")
    _validate_private_key_text(private_text, source=str(identity_path))
    return public_key_from_private_path(identity_path, source=str(identity_path))


def age_encrypt_text(plaintext: str, recipients: str) -> str:
    completed = run_command(
        ["age", "--encrypt", "--armor", *_recipient_args(recipients)],
        input_text=plaintext,
    )
    return completed.stdout


def age_decrypt_text(ciphertext: str, identity_text: str) -> str:
    with _identity_file(identity_text) as identity_path:
        completed = run_command(
            ["age", "--decrypt", "-i", str(identity_path)],
            input_text=ciphertext,
        )
    return completed.stdout


def _master_identity_text(config: WorkstationConfig, *, master_private_key_text: str | None = None) -> str:
    if master_private_key_text is not None:
        _validate_private_key_text(master_private_key_text, source="secret provider master identity")
        return master_private_key_text
    from .provider_keys import age_master_identity_path

    identity_path = age_master_identity_path(config)
    if not identity_path.is_file():
        raise ConfigError(f"age master SSH identity not found: {identity_path}")
    private_text = identity_path.read_text(encoding="utf-8", errors="replace")
    _validate_private_key_text(private_text, source=str(identity_path))
    return private_text


def decrypt_age_file_text(
    config: WorkstationConfig,
    store: Path,
    *,
    master_private_key_text: str | None = None,
) -> str:
    return age_decrypt_text(
        store.read_text(encoding="utf-8"),
        _master_identity_text(config, master_private_key_text=master_private_key_text),
    )


def secret_bundle_payload(data: dict[str, str], recipients: str) -> dict[str, object]:
    return {
        "schema": SECRET_BUNDLE_SCHEMA,
        "secrets": {
            name: {"age": age_encrypt_text(value, recipients)}
            for name, value in sorted(data.items())
        },
    }


def write_secret_bundle_map(path: Path, recipients: str, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(secret_bundle_payload(data, recipients), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def read_secret_bundle_map(
    config: WorkstationConfig,
    path: Path,
    *,
    master_private_key_text: str | None = None,
) -> dict[str, str] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != SECRET_BUNDLE_SCHEMA:
        raise PlanningError(f"invalid proxnix secret bundle: {path}")
    secrets = payload.get("secrets")
    if not isinstance(secrets, dict):
        raise PlanningError(f"invalid proxnix secret bundle: {path}")
    identity_text = _master_identity_text(config, master_private_key_text=master_private_key_text)
    result: dict[str, str] = {}
    for name, entry in secrets.items():
        if not isinstance(name, str) or not isinstance(entry, dict) or not isinstance(entry.get("age"), str):
            raise PlanningError(f"invalid secret entry in {path}: {name}")
        result[name] = age_decrypt_text(entry["age"], identity_text)
    return result


def decrypt_identity_to_file(
    config: WorkstationConfig,
    store: Path,
    destination: Path,
    *,
    master_private_key_text: str | None = None,
) -> None:
    identity_text = decrypt_identity_text(config, store, master_private_key_text=master_private_key_text)
    with destination.open("w", encoding="utf-8") as handle:
        handle.write(identity_text)
    destination.chmod(0o600)


def decrypt_identity_text(
    config: WorkstationConfig,
    store: Path,
    *,
    master_private_key_text: str | None = None,
) -> str:
    return decrypt_age_file_text(config, store, master_private_key_text=master_private_key_text)


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
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(age_encrypt_text(identity_text, recipients), encoding="utf-8")
    destination.chmod(0o600)


def reencrypt_identity_store_to_file(
    config: WorkstationConfig,
    source_store: Path,
    recipients: str,
    destination: Path,
    *,
    master_private_key_text: str | None = None,
) -> None:
    payload = decrypt_age_file_text(config, source_store, master_private_key_text=master_private_key_text)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(age_encrypt_text(payload, recipients), encoding="utf-8")
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
