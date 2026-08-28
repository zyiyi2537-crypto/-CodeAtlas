from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class CredentialEncryptionError(RuntimeError):
    """Raised when the shared provider-credential key or ciphertext is invalid."""


def _fernet(data_dir: Path) -> Fernet:
    key_path = data_dir / ".llm-config.key"
    try:
        key = key_path.read_bytes().strip()
    except FileNotFoundError:
        candidate = Fernet.generate_key()
        temporary = key_path.with_name(
            f"{key_path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        )
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as output:
                output.write(candidate)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, key_path)
            except FileExistsError:
                key = key_path.read_bytes().strip()
            else:
                key = candidate
        finally:
            temporary.unlink(missing_ok=True)
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise CredentialEncryptionError("provider encryption key is invalid") from exc


def encrypt_secret(data_dir: Path, plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet(data_dir).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(data_dir: Path, ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet(data_dir).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise CredentialEncryptionError("stored provider credential cannot be decrypted") from exc
