from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from typing import Any, cast

# Alibaba Cloud Linux links Python to an older system SQLite. Chroma requires
# pysqlite3 there even though CodeAtlas business data is stored in MySQL.
if sys.platform.startswith("linux"):
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3

import chromadb

from .chunker import CodeChunk
from .embeddings import EmbeddingClient
from .models import DEFAULT_SPACE_ID
from .settings import Settings


@dataclass(frozen=True)
class KnowledgeVectorChunk:
    id: str
    content: str
    metadata: dict[str, str | int | float | bool]


def _safe_namespace(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return (normalized[:36] or "default") + "-" + hashlib.sha256(value.encode()).hexdigest()[:8]


def _collection_name(namespace: str, dimension: int) -> str:
    if namespace == "default":
        return "codeatlas_chunks"
    return f"codeatlas_{_safe_namespace(namespace)}_{dimension}"


def code_generation_namespace(profile_namespace: str, generation_id: str) -> str:
    return f"{profile_namespace}:code:{generation_id}"


def _profile_collections(settings: Settings, profile_namespace: str) -> list[Any]:
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(settings.chroma_path))
    prefix = f"{profile_namespace}:code:"
    return [
        collection
        for collection in client.list_collections()
        if str((collection.metadata or {}).get("embedding_namespace", ""))
        == profile_namespace
        or str((collection.metadata or {}).get("embedding_namespace", "")).startswith(
            prefix
        )
    ]


def profile_contains_generation(
    settings: Settings,
    profile_namespace: str,
    generation_ids: list[str],
) -> bool:
    if not generation_ids:
        return False
    generation_namespaces = {
        code_generation_namespace(profile_namespace, generation_id)
        for generation_id in generation_ids
    }
    for collection in _profile_collections(settings, profile_namespace):
        namespace = str((collection.metadata or {}).get("embedding_namespace", ""))
        if namespace in generation_namespaces:
            return True
        if namespace == profile_namespace and collection.count():
            for generation_id in generation_ids:
                rows = collection.get(
                    where={"generation_id": generation_id},
                    include=[],
                )
                if rows.get("ids"):
                    return True
    return False


def delete_profile_collections(settings: Settings, profile_namespace: str) -> int:
    client = chromadb.PersistentClient(path=str(settings.chroma_path))
    names = [
        collection.name
        for collection in _profile_collections(settings, profile_namespace)
    ]
    for name in names:
        client.delete_collection(name)
    return len(names)


class VectorStore:
    def __init__(self, settings: Settings, namespace: str = "default"):
        self.settings = settings
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(settings.chroma_path))
        self.namespace = namespace
        self.collection = self.client.get_or_create_collection(
            _collection_name(namespace, settings.embedding_dimension),
            metadata={
                "hnsw:space": "cosine",
                "embedding_dimension": settings.embedding_dimension,
                "embedding_model": settings.embedding_model,
                "embedding_namespace": namespace,
            },
        )
        metadata = self.collection.metadata or {}
        configured_dimension = int(
            metadata.get("embedding_dimension", settings.embedding_dimension)
        )
        if configured_dimension != settings.embedding_dimension:
            raise ValueError("Chroma collection embedding dimension does not match configuration")

    def has_namespace(self, namespace: str) -> bool:
        name = _collection_name(namespace, self.settings.embedding_dimension)
        return any(collection.name == name for collection in self.client.list_collections())

    def delete_namespace(self, namespace: str) -> None:
        name = _collection_name(namespace, self.settings.embedding_dimension)
        if any(collection.name == name for collection in self.client.list_collections()):
            self.client.delete_collection(name)

    def add_generation(
        self,
        chunks: list[CodeChunk],
        embedder: EmbeddingClient,
        batch_size: int = 32,
        space_id: str = DEFAULT_SPACE_ID,
    ) -> None:
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            documents = [chunk.content for chunk in batch]
            self.collection.upsert(
                ids=[chunk.id for chunk in batch],
                documents=documents,
                embeddings=cast(Any, embedder.embed(documents)),
                metadatas=[{
                    "source_type": "code",
                    "source_id": chunk.repository_id,
                    "collection_id": "",
                    "space_id": space_id,
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

    def add_knowledge(
        self,
        chunks: list[KnowledgeVectorChunk],
        embedder: EmbeddingClient,
        batch_size: int = 32,
    ) -> None:
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            documents = [chunk.content for chunk in batch]
            self.collection.upsert(
                ids=[chunk.id for chunk in batch],
                documents=documents,
                embeddings=cast(Any, embedder.embed(documents)),
                metadatas=cast(Any, [chunk.metadata for chunk in batch]),
            )

    def delete_source(self, source_type: str, source_id: str) -> None:
        if self.collection.count():
            self.collection.delete(
                where={"$and": [{"source_type": source_type}, {"source_id": source_id}]}
            )

    def search_knowledge(
        self,
        query_embedding: list[float],
        source_types: list[str],
        limit: int,
        collection_ids: list[str] | None = None,
        space_ids: list[str] | None = None,
    ) -> list[dict]:
        if not source_types or not self.collection.count():
            return []
        clauses: list[dict] = [
            {"source_type": source_types[0]}
            if len(source_types) == 1
            else {"source_type": {"$in": source_types}}
        ]
        if collection_ids:
            clauses.append(
                {"collection_id": collection_ids[0]}
                if len(collection_ids) == 1
                else {"collection_id": {"$in": collection_ids}}
            )
        if space_ids:
            clauses.append(
                {"space_id": space_ids[0]}
                if len(space_ids) == 1
                else {"space_id": {"$in": space_ids}}
            )
        def query(where: dict, result_limit: int) -> list[dict]:
            result = self.collection.query(
                query_embeddings=cast(Any, [query_embedding]),
                n_results=min(max(1, result_limit), self.collection.count()),
                where=cast(Any, where),
                include=["documents", "metadatas", "distances"],
            )
            return [
                {
                    "id": item_id,
                    "document": document,
                    "metadata": metadata,
                    "vector_score": max(0.0, 1.0 - float(distance)),
                }
                for item_id, document, metadata, distance in zip(
                    (result.get("ids") or [[]])[0],
                    (result.get("documents") or [[]])[0],
                    (result.get("metadatas") or [[]])[0],
                    (result.get("distances") or [[]])[0],
                    strict=True,
                )
            ]

        where = clauses[0] if len(clauses) == 1 else {"$and": clauses}
        candidates = query(where, limit)
        if space_ids and DEFAULT_SPACE_ID in space_ids:
            legacy_clauses = clauses[:-1]
            legacy_where = (
                legacy_clauses[0]
                if len(legacy_clauses) == 1
                else {"$and": legacy_clauses}
            )
            legacy_candidates = query(legacy_where, max(limit * 3, 20))
            candidates.extend(
                candidate
                for candidate in legacy_candidates
                if not candidate["metadata"].get("space_id")
            )
        by_id = {candidate["id"]: candidate for candidate in candidates}
        return sorted(
            by_id.values(),
            key=lambda candidate: float(candidate["vector_score"]),
            reverse=True,
        )[:limit]

    def delete_generation(self, generation_id: str) -> None:
        if self.collection.count():
            self.collection.delete(where={"generation_id": generation_id})

    def count_knowledge(self) -> int:
        if not self.collection.count():
            return 0
        rows = self.collection.get(
            where=cast(Any, {"source_type": {"$in": ["document", "wiki"]}}),
            include=[],
        )
        return len(rows.get("ids") or [])

    def search(
        self,
        query_embedding: list[float],
        generation_ids: list[str],
        candidate_limit: int,
        languages: list[str] | None = None,
    ) -> list[dict]:
        if not generation_ids or not self.collection.count():
            return []
        generation_filter = (
            {"generation_id": generation_ids[0]}
            if len(generation_ids) == 1
            else {"generation_id": {"$in": generation_ids}}
        )
        normalized_languages = sorted(
            {language.lower() for language in (languages or [])}
        )
        where: dict[str, Any]
        if normalized_languages:
            language_filter = (
                {"language": normalized_languages[0]}
                if len(normalized_languages) == 1
                else {"language": {"$in": normalized_languages}}
            )
            where = {"$and": [generation_filter, language_filter]}
        else:
            where = generation_filter
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
