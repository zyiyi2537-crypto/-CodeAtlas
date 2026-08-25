from __future__ import annotations

import json
from dataclasses import dataclass

from sqlmodel import Session, col, select

from .documents import StructuredBlock, split_structured_blocks
from .embeddings import EmbeddingClient
from .models import Document, DocumentChunkRecord, EmbeddingProfile, WikiPage
from .settings import Settings
from .vector_store import KnowledgeVectorChunk, VectorStore


@dataclass(frozen=True)
class _EmbeddingContext:
    settings: Settings
    vector_store: VectorStore
    embedder: EmbeddingClient


class KnowledgeSearch:
    """Unified structural/semantic retrieval for documents and Wiki pages."""

    def __init__(self, engine, settings: Settings | None = None):
        self.engine = engine
        self.settings = settings
        self.refresh_embedding_context()

    def refresh_embedding_context(self) -> None:
        vector_settings, namespace = self._embedding_context()
        self._context = _EmbeddingContext(
            settings=vector_settings,
            vector_store=VectorStore(vector_settings, namespace=namespace),
            embedder=EmbeddingClient(vector_settings),
        )

    @property
    def vector_settings(self) -> Settings:
        return self._context.settings

    @property
    def vector_store(self) -> VectorStore:
        return self._context.vector_store

    @property
    def embedder(self) -> EmbeddingClient:
        return self._context.embedder

    def _embedding_context(self) -> tuple[Settings, str]:
        if self.settings is None:
            raise ValueError("KnowledgeSearch requires application settings")
        try:
            from .embeddings import settings_for_profile

            with Session(self.engine) as session:
                profile = session.exec(
                    select(EmbeddingProfile).where(EmbeddingProfile.is_active)
                ).first()
            if profile:
                return settings_for_profile(self.settings, profile), profile.id
        except (AttributeError, TypeError):
            pass
        return self.settings, "default"

    @staticmethod
    def _terms(query: str) -> list[str]:
        normalized = query.strip()
        if not normalized or len(normalized) > 500:
            raise ValueError("query must contain between 1 and 500 characters")
        return [term.lower() for term in normalized.split() if term.strip()]

    def _document_rows(
        self, collection_ids: list[str] | tuple[str, ...] | None = None
    ) -> list[DocumentChunkRecord]:
        with Session(self.engine) as session:
            indexed_document_ids = session.exec(
                select(Document.id).where(Document.status == "indexed")
            ).all()
            if not indexed_document_ids:
                return []
            statement = select(DocumentChunkRecord)
            statement = statement.where(
                col(DocumentChunkRecord.document_id).in_(indexed_document_ids)
            )
            if collection_ids:
                statement = statement.where(
                    col(DocumentChunkRecord.collection_id).in_(collection_ids)
                )
            return list(session.exec(statement).all())

    def _wiki_rows(self) -> list[WikiPage]:
        with Session(self.engine) as session:
            return list(
                session.exec(select(WikiPage).where(WikiPage.status == "published")).all()
            )

    def _indexed_document_ids(self) -> set[str]:
        with Session(self.engine) as session:
            return set(
                session.exec(
                    select(Document.id).where(Document.status == "indexed")
                ).all()
            )

    def index_document(self, chunks: list[DocumentChunkRecord]) -> None:
        context = self._context
        if chunks:
            context.vector_store.delete_source("document", chunks[0].document_id)
        vectors = []
        for chunk in chunks:
            metadata = json.loads(chunk.metadata_json or "{}")
            vectors.append(
                KnowledgeVectorChunk(
                    id=chunk.id,
                    content=chunk.content,
                    metadata={
                        "source_type": "document",
                        "source_id": chunk.document_id,
                        "collection_id": chunk.collection_id,
                        "title": chunk.title,
                        "section": chunk.section,
                        "page": chunk.page or 0,
                        "structure_type": chunk.structure_type,
                        "ordinal": int(metadata.get("ordinal", 0)),
                        "external_provider": str(metadata.get("external_provider", "")),
                        "external_source_id": str(metadata.get("external_source_id", "")),
                        "external_id": str(metadata.get("external_id", "")),
                        "source_url": str(metadata.get("source_url", "")),
                        "external_path": str(metadata.get("external_path", "")),
                    },
                )
            )
        if vectors:
            context.vector_store.add_knowledge(vectors, context.embedder)

    def index_wiki(self, page: WikiPage) -> None:
        context = self._context
        blocks = self._wiki_blocks(page)
        chunks = split_structured_blocks(page.title, blocks)
        vectors = [
            KnowledgeVectorChunk(
                id=f"wiki:{page.id}:{index}",
                content=chunk.content,
                metadata={
                    "source_type": "wiki",
                    "source_id": page.id,
                    "collection_id": "",
                    "title": page.title,
                    "section": chunk.section,
                    "page": 0,
                    "structure_type": chunk.structure_type,
                    "path": page.path,
                },
            )
            for index, chunk in enumerate(chunks, start=1)
        ]
        context.vector_store.delete_source("wiki", page.id)
        if vectors:
            context.vector_store.add_knowledge(vectors, context.embedder)

    def rebuild_all(self) -> dict[str, int]:
        document_chunks = self._document_rows()
        chunks_by_document: dict[str, list[DocumentChunkRecord]] = {}
        for chunk in document_chunks:
            chunks_by_document.setdefault(chunk.document_id, []).append(chunk)
        for chunks in chunks_by_document.values():
            self.index_document(chunks)
        wiki_pages = self._wiki_rows()
        for page in wiki_pages:
            self.index_wiki(page)
        return {
            "document_chunks": len(document_chunks),
            "wiki_pages": len(wiki_pages),
        }

    @staticmethod
    def _wiki_blocks(page: WikiPage) -> list[StructuredBlock]:
        from .documents import extract_structured_blocks

        return extract_structured_blocks(page.path, page.content.encode("utf-8"))

    def search(
        self,
        query: str,
        source_types: list[str] | None = None,
        collection_ids: list[str] | tuple[str, ...] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        context = self._context
        terms = self._terms(query)
        wanted = source_types or ["document", "wiki"]
        lexical: dict[str, dict] = {}
        if "document" in wanted:
            for row in self._document_rows(collection_ids):
                metadata = json.loads(row.metadata_json or "{}")
                score = self._lexical_score(terms, f"{row.title} {row.section} {row.content}")
                if score:
                    lexical[row.id] = {
                        "id": row.id,
                        "source_type": "document",
                        "source_id": row.document_id,
                        "collection_id": row.collection_id,
                        "title": row.title,
                        "section": row.section,
                        "page": row.page,
                        "content": row.content,
                        "external_provider": metadata.get("external_provider", ""),
                        "external_source_id": metadata.get("external_source_id", ""),
                        "external_id": metadata.get("external_id", ""),
                        "source_url": metadata.get("source_url", ""),
                        "external_path": metadata.get("external_path", ""),
                        "lexical_score": score,
                    }
        if "wiki" in wanted:
            for page in self._wiki_rows():
                score = self._lexical_score(terms, f"{page.title} {page.content}")
                if score:
                    lexical[f"wiki:{page.id}:1"] = {
                        "id": f"wiki:{page.id}:1",
                        "source_type": "wiki",
                        "source_id": page.id,
                        "collection_id": "",
                        "title": page.title,
                        "section": page.path,
                        "page": None,
                        "path": page.path,
                        "sources": json.loads(page.sources_json),
                        "content": page.content,
                        "lexical_score": score,
                    }
        vector = context.vector_store.search_knowledge(
            context.embedder.embed([query])[0],
            wanted,
            max(limit * 3, 20),
            list(collection_ids) if collection_ids else None,
        )
        if "document" in wanted:
            indexed_document_ids = self._indexed_document_ids()
            vector = [
                candidate
                for candidate in vector
                if candidate["metadata"].get("source_type") != "document"
                or candidate["metadata"].get("source_id") in indexed_document_ids
            ]
        pool: dict[str, dict] = dict(lexical)
        for rank, candidate in enumerate(vector, start=1):
            metadata = candidate["metadata"]
            item = pool.setdefault(
                candidate["id"],
                {
                    "id": candidate["id"],
                    "source_type": metadata.get("source_type", "document"),
                    "source_id": metadata.get("source_id", ""),
                    "collection_id": metadata.get("collection_id", ""),
                    "title": metadata.get("title", ""),
                    "section": metadata.get("section", ""),
                    "page": metadata.get("page") or None,
                    "path": metadata.get("path", ""),
                    "external_provider": metadata.get("external_provider", ""),
                    "external_source_id": metadata.get("external_source_id", ""),
                    "external_id": metadata.get("external_id", ""),
                    "source_url": metadata.get("source_url", ""),
                    "external_path": metadata.get("external_path", ""),
                    "content": candidate["document"],
                    "lexical_score": 0,
                },
            )
            item["vector_score"] = candidate["vector_score"]
            item["vector_rank"] = rank
        for item in pool.values():
            vector_score = float(item.get("vector_score", 0))
            lexical_score = float(item.get("lexical_score", 0))
            item["retrieval"] = (
                "hybrid"
                if vector_score and lexical_score
                else "vector" if vector_score else "lexical"
            )
            item["score"] = vector_score + min(1.0, lexical_score / 5.0)
        return sorted(pool.values(), key=lambda item: item["score"], reverse=True)[:limit]

    @staticmethod
    def _lexical_score(terms: list[str], text: str) -> int:
        haystack = text.lower()
        return sum(haystack.count(term) for term in terms)

    def search_documents(
        self, query: str, collection_ids: list[str] | tuple[str, ...] | None = None
    ) -> list[dict]:
        results = self.search(
            query,
            source_types=["document"],
            collection_ids=collection_ids,
        )
        return [
            {**item, "document_id": item["source_id"]}
            for item in results
        ]

    def search_wiki(self, query: str) -> list[dict]:
        return self.search(query, source_types=["wiki"])

    def get_wiki_page(self, path: str) -> dict:
        with Session(self.engine) as session:
            page = session.exec(
                select(WikiPage).where(
                    WikiPage.path == path,
                    WikiPage.status == "published",
                )
            ).first()
        if page is None:
            raise FileNotFoundError(path)
        return {
            "source_type": "wiki",
            "path": page.path,
            "title": page.title,
            "content": page.content,
            "sources": json.loads(page.sources_json),
        }
