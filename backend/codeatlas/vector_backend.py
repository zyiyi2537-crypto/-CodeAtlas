from __future__ import annotations

from typing import Protocol

from .chunker import CodeChunk
from .embeddings import EmbeddingClient
from .settings import Settings
from .vector_store import VectorStore


class VectorBackend(Protocol):
    def add_generation(self, chunks: list[CodeChunk], embedder: EmbeddingClient) -> None: ...
    def delete_generation(self, generation_id: str) -> None: ...
    def count(self) -> int: ...


class ChromaVectorBackend:
    """Vector backend adapter; Milvus is intentionally not implemented yet."""

    name = "chroma"

    def __init__(self, settings: Settings):
        self.store = VectorStore(settings)

    def add_generation(self, chunks: list[CodeChunk], embedder: EmbeddingClient) -> None:
        self.store.add_generation(chunks, embedder)

    def delete_generation(self, generation_id: str) -> None:
        self.store.delete_generation(generation_id)

    def count(self) -> int:
        return self.store.count()


def create_vector_backend(settings: Settings, backend: str = "chroma") -> ChromaVectorBackend:
    if backend != "chroma":
        raise ValueError("Only the Chroma vector backend is implemented currently")
    return ChromaVectorBackend(settings)
