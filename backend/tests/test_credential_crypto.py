from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from cryptography.fernet import Fernet

from codeatlas.credential_crypto import decrypt_secret, encrypt_secret


def test_provider_encryption_key_creation_is_atomic(tmp_path, monkeypatch) -> None:
    generated_keys = [Fernet.generate_key(), Fernet.generate_key()]
    barrier = threading.Barrier(2)
    key_lock = threading.Lock()

    def synchronized_generate_key() -> bytes:
        barrier.wait(timeout=5)
        with key_lock:
            return generated_keys.pop()

    monkeypatch.setattr(
        Fernet,
        "generate_key",
        staticmethod(synchronized_generate_key),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        ciphertexts = list(
            executor.map(
                lambda index: encrypt_secret(tmp_path, f"provider-secret-{index}"),
                range(2),
            )
        )

    assert [decrypt_secret(tmp_path, value) for value in ciphertexts] == [
        "provider-secret-0",
        "provider-secret-1",
    ]
    assert (tmp_path / ".llm-config.key").stat().st_size == 44
