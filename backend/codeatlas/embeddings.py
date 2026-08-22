from __future__ import annotations

import hashlib
import math
import re
import time

import httpx

from .settings import Settings

_TOKEN_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$.-]*|[\u4e00-\u9fff]+|\d+")


class EmbeddingClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.embedding_mode not in {"hash", "openai"}:
            raise ValueError("CODEATLAS_EMBEDDING_MODE must be hash or openai")
        if settings.embedding_mode == "openai" and (
            not settings.embedding_base_url or not settings.embedding_api_key
        ):
            raise ValueError("OpenAI-compatible embedding mode requires a base URL and API key")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.settings.embedding_mode == "hash":
            return [self._hash_embedding(text) for text in texts]
        return self._embed_with_retry(texts)

    def _embed_with_retry(self, texts: list[str], max_retries: int = 3) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                return self._embed_batch(texts)
            except (httpx.HTTPError, ValueError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        raise last_error or RuntimeError("embedding failed")

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            f"{self.settings.embedding_base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.settings.embedding_api_key}"},
            json={"model": self.settings.embedding_model, "input": texts},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        ordered = sorted(payload.get("data", []), key=lambda item: int(item.get("index", 0)))
        embeddings = [item["embedding"] for item in ordered]
        if len(embeddings) != len(texts):
            raise ValueError("embedding provider returned an unexpected result count")
        for embedding in embeddings:
            if len(embedding) != self.settings.embedding_dimension:
                raise ValueError("embedding provider returned an unexpected dimension")
        return embeddings

    def _hash_embedding(self, text: str) -> list[float]:
        values = [0.0] * self.settings.embedding_dimension
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % len(values)
            sign = 1.0 if digest[4] & 1 else -1.0
            values[index] += sign * (1.0 + min(len(token), 20) / 20)
        magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / magnitude for value in values]
