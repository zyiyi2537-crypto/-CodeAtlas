from __future__ import annotations

import hashlib
import math
import os
import re
import time
from dataclasses import replace
from typing import TYPE_CHECKING

import httpx

from .settings import Settings

if TYPE_CHECKING:
    from .models import EmbeddingProfile

_TOKEN_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$.-]*|[\u4e00-\u9fff]+|\d+")


def embedding_credential_name(credential_ref: str) -> str:
    normalized = credential_ref.strip().upper().replace("-", "_")
    return f"CODEATLAS_CREDENTIAL_{normalized}"


def resolve_embedding_api_key(credential_ref: str) -> str:
    """Resolve a profile reference without ever persisting the secret."""
    environment_name = embedding_credential_name(credential_ref)
    return os.getenv(environment_name, "").strip()


def settings_for_profile(settings: Settings, profile: EmbeddingProfile) -> Settings:
    api_key = resolve_embedding_api_key(profile.credential_ref)
    if not api_key:
        raise ValueError(
            f"Embedding credential is not configured on the server: "
            f"{embedding_credential_name(profile.credential_ref)}"
        )
    return replace(
        settings,
        embedding_mode=(
            "tencent_multimodal"
            if profile.provider == "tencent_multimodal"
            else "openai"
        ),
        embedding_base_url=profile.base_url.rstrip("/"),
        embedding_api_key=api_key,
        embedding_model=profile.model,
        embedding_dimension=profile.dimension,
    )


class EmbeddingClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        allowed_modes = {"hash", "openai", "tencent_multimodal"}
        if settings.embedding_mode not in allowed_modes:
            raise ValueError(
                "CODEATLAS_EMBEDDING_MODE must be hash, openai or tencent_multimodal"
            )
        if settings.embedding_mode != "hash" and (
            not settings.embedding_base_url or not settings.embedding_api_key
        ):
            raise ValueError("Remote embedding mode requires a base URL and API key")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.settings.embedding_mode == "hash":
            return [self._hash_embedding(text) for text in texts]
        if self.settings.embedding_mode == "tencent_multimodal":
            return self._embed_tencent_texts(texts)
        return self._embed_with_retry(texts)

    def probe_dimension(self, text: str = "CodeAtlas 向量维度探测") -> int:
        if self.settings.embedding_mode == "tencent_multimodal":
            payload = self._post_json(
                f"{self.settings.embedding_base_url}/embeddings/multimodal",
                self._tencent_payload(text),
            )
            return len(self._extract_single_embedding(payload))
        return len(self.embed([text])[0])

    def _embed_tencent_texts(
        self, texts: list[str], max_retries: int = 3
    ) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for text in texts:
            last_error: Exception | None = None
            for attempt in range(max_retries):
                try:
                    payload = self._post_json(
                        f"{self.settings.embedding_base_url}/embeddings/multimodal",
                        self._tencent_payload(text),
                    )
                    embedding = self._extract_single_embedding(payload)
                    self._validate_dimensions([embedding])
                    embeddings.append(embedding)
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = exc
                    if attempt < max_retries - 1:
                        time.sleep(2**attempt)
            else:
                raise last_error or RuntimeError("embedding failed")
        return embeddings

    def _tencent_payload(self, text: str) -> dict:
        return {
            "model": self.settings.embedding_model,
            "input": [{"type": "text", "text": text}],
            "instructions": "生成适合文本检索的向量",
        }

    def _post_json(self, url: str, payload: dict) -> dict:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {self.settings.embedding_api_key}"},
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _extract_single_embedding(payload: dict) -> list[float]:
        if isinstance(payload.get("embedding"), list):
            return payload["embedding"]
        data = payload.get("data") or []
        if data and isinstance(data[0].get("embedding"), list):
            return data[0]["embedding"]
        raise ValueError("embedding provider returned no embedding")

    def _embed_with_retry(
        self, texts: list[str], max_retries: int = 3
    ) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                return self._embed_batch(texts)
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    time.sleep(2**attempt)
        raise last_error or RuntimeError("embedding failed")

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = self._post_json(
            f"{self.settings.embedding_base_url}/embeddings",
            {"model": self.settings.embedding_model, "input": texts},
        )
        ordered = sorted(payload.get("data", []), key=lambda item: int(item.get("index", 0)))
        embeddings = [item["embedding"] for item in ordered]
        if len(embeddings) != len(texts):
            raise ValueError("embedding provider returned an unexpected result count")
        self._validate_dimensions(embeddings)
        return embeddings

    def _validate_dimensions(self, embeddings: list[list[float]]) -> None:
        for embedding in embeddings:
            if len(embedding) != self.settings.embedding_dimension:
                raise ValueError("embedding provider returned an unexpected dimension")

    def _hash_embedding(self, text: str) -> list[float]:
        values = [0.0] * self.settings.embedding_dimension
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % len(values)
            sign = 1.0 if digest[4] & 1 else -1.0
            values[index] += sign * (1.0 + min(len(token), 20) / 20)
        magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / magnitude for value in values]
