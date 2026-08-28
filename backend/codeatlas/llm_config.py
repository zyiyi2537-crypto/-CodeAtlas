from __future__ import annotations

import secrets
from pathlib import Path

import httpx

from .credential_crypto import (
    CredentialEncryptionError,
    decrypt_secret,
    encrypt_secret,
)
from .models import LlmProvider


class LlmProviderError(RuntimeError):
    """Raised when an LLM provider configuration or upstream call is invalid."""


def encrypt_api_key(data_dir: Path, api_key: str) -> str:
    try:
        return encrypt_secret(data_dir, api_key)
    except CredentialEncryptionError as exc:
        raise LlmProviderError(str(exc)) from exc


def decrypt_api_key(data_dir: Path, provider: LlmProvider) -> str:
    try:
        return decrypt_secret(data_dir, provider.api_key_ciphertext)
    except CredentialEncryptionError as exc:
        raise LlmProviderError(str(exc)) from exc


def normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized.startswith(("http://", "https://")):
        raise ValueError("Base URL must start with http:// or https://")
    if any(character.isspace() for character in normalized):
        raise ValueError("Base URL must not contain whitespace")
    return normalized


def sync_models(base_url: str, api_key: str) -> list[dict[str, str]]:
    url = f"{normalize_base_url(base_url)}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = httpx.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LlmProviderError(f"failed to sync upstream models: {exc}") from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise LlmProviderError("upstream /models response does not contain a data list")
    models: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        model_id = str(row["id"])
        models.append({"id": model_id, "name": str(row.get("name") or model_id)})
    return sorted(models, key=lambda item: item["id"].lower())


def new_provider_name(base_url: str) -> str:
    host = base_url.split("//", 1)[-1].split("/", 1)[0]
    return f"{host}-{secrets.token_hex(3)}"
