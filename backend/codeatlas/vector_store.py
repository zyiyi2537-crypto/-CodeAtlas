from __future__ import annotations

import sys
from typing import Any, cast

# Alibaba Cloud Linux links Python to an older system SQLite. Chroma requires
# pysqlite3 there even though CodeAtlas business data is stored in MySQL.
if sys.platform.startswith("linux"):
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3

import chromadb

from .chunker import CodeChunk
from .embeddings import EmbeddingClient
from .settings import Settings


class VectorStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(settings.chroma_path))
        self.collection = self.client.get_or_create_collection(
            "codeatlas_chunks",
            metadata={
                "hnsw:space": "cosine",
                "embedding_dimension": settings.embedding_dimension,
                "embedding_model": settings.embedding_model,
            },
        )
        metadata = self.collection.metadata or {}
        configured_dimension = int(
            metadata.get("embedding_dimension", settings.embedding_dimension)
        )
        if configured_dimension != settings.embedding_dimension:
            raise ValueError("Chroma collection embedding dimension does not match configuration")

    def add_generation(
        self, chunks: list[CodeChunk], embedder: EmbeddingClient, batch_size: int = 32
    ) -> None:
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            documents = [chunk.content for chunk in batch]
            self.collection.upsert(
                ids=[chunk.id for chunk in batch],
                documents=documents,
                embeddings=cast(Any, embedder.embed(documents)),
                metadatas=[{
                    "repo": chunk.repository_id,
                    "generation_id": chunk.generation_id,
                    "commit": chunk.commit,
                    "path": chunk.path,
                    "language": chunk.language,
                    "symbol": chunk.symbol,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                } for chunk in batch],
            )

    def delete_generation(self, generation_id: str) -> None:
        if self.collection.count():
            self.collection.delete(where={"generation_id": generation_id})

    def search(
        self,
        query_embedding: list[float],
        generation_ids: list[str],
        candidate_limit: int,
    ) -> list[dict]:
        if not generation_ids or not self.collection.count():
            return []
        where = (
            {"generation_id": generation_ids[0]}
            if len(generation_ids) == 1
            else {"generation_id": {"$in": generation_ids}}
        )
        result = self.collection.query(
            query_embeddings=cast(Any, [query_embedding]),
            n_results=min(candidate_limit, self.collection.count()),
            where=cast(Any, where),
            include=["documents", "metadatas", "distances"],
        )
        candidates = []
        for item_id, document, metadata, distance in zip(
            (result.get("ids") or [[]])[0],
            (result.get("documents") or [[]])[0],
            (result.get("metadatas") or [[]])[0],
            (result.get("distances") or [[]])[0],
            strict=True,
        ):
            candidates.append({
                "id": item_id,
                "document": document,
                "metadata": metadata,
                "vector_score": max(0.0, 1.0 - float(distance)),
            })
        return candidates

    def count(self) -> int:
        return self.collection.count()
