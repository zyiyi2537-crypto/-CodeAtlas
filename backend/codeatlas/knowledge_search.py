from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlmodel import Session, col, select

from .authorization import AuthorizationScope
from .documents import StructuredBlock, split_structured_blocks
from .embeddings import EmbeddingClient
from .models import Document, DocumentChunkRecord, EmbeddingProfile, WikiPage
from .ranking import RRF_K
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

    @staticmethod
    def _boolean_query(terms: list[str]) -> str:
        return " ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:12])

    @staticmethod
    def _structure_fields(metadata: dict) -> dict:
        sources = metadata.get("sources", [])
        if not sources and metadata.get("sources_json"):
            try:
                sources = json.loads(str(metadata["sources_json"]))
            except json.JSONDecodeError:
                sources = []
        def coordinate(name: str) -> int | None:
            value = metadata.get(name)
            return int(str(value)) if value not in {None, "", 0, "0"} else None

        return {
            "structure_type": metadata.get("structure_type", ""),
            "sheet": metadata.get("sheet", ""),
            "row_start": coordinate("row_start"),
            "row_end": coordinate("row_end"),
            "slide": coordinate("slide"),
            "sources": sources if isinstance(sources, list) else [],
        }

    def _document_lexical_candidates(
        self,
        terms: list[str],
        collection_ids: list[str] | tuple[str, ...] | None,
        limit: int,
    ) -> list[dict]:
        if not terms:
            return []
        clauses = ["d.status = 'indexed'"]
        parameters: dict[str, str | int] = {
            "query": self._boolean_query(terms),
            "limit": limit,
        }
        if collection_ids:
            placeholders = []
            for index, collection_id in enumerate(collection_ids):
                key = f"collection_{index}"
                placeholders.append(f":{key}")
                parameters[key] = collection_id
            clauses.append(f"c.collection_id IN ({','.join(placeholders)})")
        statement = text(f"""
            SELECT c.*,
                   MATCH(c.title, c.section, c.content)
                   AGAINST (:query IN BOOLEAN MODE) AS lexical_rank
            FROM documentchunkrecord c
            JOIN document d ON d.id = c.document_id
            WHERE {' AND '.join(clauses)}
              AND MATCH(c.title, c.section, c.content)
                  AGAINST (:query IN BOOLEAN MODE)
            ORDER BY lexical_rank DESC
            LIMIT :limit
        """)
        with self.engine.connect() as connection:
            rows = connection.execute(statement, parameters).mappings().all()
        candidates = []
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            candidates.append(
                {
                    "id": row["id"],
                    "source_type": "document",
                    "source_id": row["document_id"],
                    "collection_id": row["collection_id"],
                    "title": row["title"],
                    "section": row["section"],
                    "page": row["page"],
                    "content": row["content"],
                    "external_provider": metadata.get("external_provider", ""),
                    "external_source_id": metadata.get("external_source_id", ""),
                    "external_id": metadata.get("external_id", ""),
                    "source_url": metadata.get("source_url", ""),
                    "external_path": metadata.get("external_path", ""),
                    **self._structure_fields(
                        {"structure_type": row["structure_type"], **metadata}
                    ),
                    "lexical_score": float(row["lexical_rank"] or 0),
                }
            )
        return candidates

    def _wiki_fulltext_pages(
        self,
        terms: list[str],
        limit: int,
        space_ids: tuple[str, ...] | None = None,
    ) -> list[WikiPage]:
        if not terms:
            return []
        clauses = ["status = 'published'"]
        parameters: dict[str, str | int] = {
            "query": self._boolean_query(terms),
            "limit": limit,
        }
        if space_ids is not None:
            if not space_ids:
                return []
            placeholders = []
            for index, space_id in enumerate(space_ids):
                key = f"space_{index}"
                placeholders.append(f":{key}")
                parameters[key] = space_id
            clauses.append(f"space_id IN ({','.join(placeholders)})")
        statement = text(f"""
            SELECT id
            FROM wikipage
            WHERE {' AND '.join(clauses)}
              AND MATCH(title, content) AGAINST (:query IN BOOLEAN MODE)
            ORDER BY MATCH(title, content) AGAINST (:query IN BOOLEAN MODE) DESC
            LIMIT :limit
        """)
        with self.engine.connect() as connection:
            ids = [
                str(row[0])
                for row in connection.execute(statement, parameters).all()
            ]
        if not ids:
            return []
        with Session(self.engine) as session:
            pages = session.exec(select(WikiPage).where(col(WikiPage.id).in_(ids))).all()
        by_id = {page.id: page for page in pages}
        return [by_id[page_id] for page_id in ids if page_id in by_id]

    def _wiki_lexical_candidates(
        self,
        terms: list[str],
        limit: int,
        space_ids: tuple[str, ...] | None = None,
    ) -> list[dict]:
        candidates = []
        for page in self._wiki_fulltext_pages(terms, limit, space_ids):
            sources = json.loads(page.sources_json or "[]")
            chunks = split_structured_blocks(
                page.title,
                self._wiki_blocks(page),
            )
            for chunk_index, chunk in enumerate(chunks, start=1):
                score = self._lexical_score(
                    terms,
                    f"{page.title} {chunk.section} {chunk.content}",
                )
                if not score:
                    continue
                candidates.append(
                    {
                        "id": f"wiki:{page.id}:{chunk_index}",
                        "source_type": "wiki",
                        "source_id": page.id,
                        "collection_id": "",
                        "title": page.title,
                        "section": chunk.section,
                        "page": chunk.page,
                        "path": page.path,
                        "sources": sources,
                        "content": chunk.content,
                        "structure_type": chunk.structure_type,
                        "sheet": "",
                        "row_start": None,
                        "row_end": None,
                        "slide": chunk.metadata.get("slide"),
                        "lexical_score": float(score),
                    }
                )
        return sorted(
            candidates,
            key=lambda candidate: float(candidate["lexical_score"]),
            reverse=True,
        )[:limit]

    def _lexical_candidates(
        self,
        terms: list[str],
        wanted: list[str],
        collection_ids: list[str] | tuple[str, ...] | None,
        limit: int,
        space_ids: tuple[str, ...] | None = None,
    ) -> list[dict]:
        candidates: list[dict] = []
        if "document" in wanted:
            for rank, candidate in enumerate(
                self._document_lexical_candidates(terms, collection_ids, limit),
                start=1,
            ):
                candidates.append(
                    {**candidate, "lexical_rank": rank, "source_rank": rank}
                )
        if "wiki" in wanted:
            for rank, candidate in enumerate(
                self._wiki_lexical_candidates(terms, limit, space_ids),
                start=1,
            ):
                candidates.append(
                    {**candidate, "lexical_rank": rank, "source_rank": rank}
                )
        return candidates

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
                        "space_id": chunk.space_id,
                        "title": chunk.title,
                        "section": chunk.section,
                        "page": chunk.page or 0,
                        "structure_type": chunk.structure_type,
                        "ordinal": int(metadata.get("ordinal", 0)),
                        "sheet": str(metadata.get("sheet", "")),
                        "row_start": int(metadata.get("row_start", 0) or 0),
                        "row_end": int(metadata.get("row_end", 0) or 0),
                        "slide": int(metadata.get("slide", 0) or 0),
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
                    "space_id": page.space_id,
                    "title": page.title,
                    "section": chunk.section,
                    "page": 0,
                    "structure_type": chunk.structure_type,
                    "path": page.path,
                    "sources_json": page.sources_json,
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
        authorization_scope: AuthorizationScope | None = None,
    ) -> list[dict]:
        context = self._context
        terms = self._terms(query)
        wanted = source_types or ["document", "wiki"]
        scoped_collection_ids = collection_ids
        scoped_space_ids: tuple[str, ...] | None = None
        if authorization_scope is not None:
            allowed_collections = set(authorization_scope.collection_ids)
            if collection_ids:
                allowed_collections.intersection_update(collection_ids)
            scoped_collection_ids = tuple(sorted(allowed_collections))
            scoped_space_ids = authorization_scope.space_ids
            if not scoped_collection_ids:
                wanted = [value for value in wanted if value != "document"]
            if not scoped_space_ids:
                wanted = [value for value in wanted if value != "wiki"]
        if not wanted:
            return []
        lexical = self._lexical_candidates(
            terms,
            wanted,
            scoped_collection_ids,
            max(limit * 3, 20),
            scoped_space_ids,
        )
        candidate_limit = max(limit * 3, 20)
        query_embedding = context.embedder.embed([query])[0]
        vector: list[dict] = []
        for source_type in ("document", "wiki"):
            if source_type not in wanted:
                continue
            lane = context.vector_store.search_knowledge(
                query_embedding,
                [source_type],
                candidate_limit,
                (
                    list(scoped_collection_ids)
                    if source_type == "document" and scoped_collection_ids
                    else None
                ),
                list(scoped_space_ids) if scoped_space_ids else None,
            )
            lane = [
                candidate
                for candidate in lane
                if float(candidate.get("vector_score", 0)) > 0
                and candidate.get("metadata", {}).get("source_type") == source_type
            ]
            for rank, candidate in enumerate(lane, start=1):
                vector.append(
                    {**candidate, "vector_rank": rank, "source_rank": rank}
                )
        if "document" in wanted:
            indexed_document_ids = self._indexed_document_ids()
            vector = [
                candidate
                for candidate in vector
                if candidate["metadata"].get("source_type") != "document"
                or candidate["metadata"].get("source_id") in indexed_document_ids
            ]
        pool: dict[str, dict] = {}
        for fallback_rank, candidate in enumerate(lexical, start=1):
            rank = int(candidate.get("lexical_rank", fallback_rank))
            item = pool.setdefault(candidate["id"], {**candidate, "rrf_score": 0.0})
            item["rrf_score"] += 0.9 / (RRF_K + rank)
            item["lexical_rank"] = rank
            item.setdefault("source_rank", rank)
        for fallback_rank, candidate in enumerate(vector, start=1):
            rank = int(candidate.get("vector_rank", fallback_rank))
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
                    "rrf_score": 0.0,
                    "source_rank": int(candidate.get("source_rank", rank)),
                    **self._structure_fields(metadata),
                },
            )
            item["vector_score"] = candidate["vector_score"]
            item["vector_rank"] = rank
            item["rrf_score"] += 1.0 / (RRF_K + rank)
            for key, value in self._structure_fields(metadata).items():
                if item.get(key) in (None, "", []) and value not in (None, "", []):
                    item[key] = value
        for item in pool.values():
            vector_score = float(item.get("vector_score", 0))
            lexical_score = float(item.get("lexical_score", 0))
            item["retrieval"] = (
                "hybrid"
                if vector_score and lexical_score
                else "vector" if vector_score else "lexical"
            )
            item["score"] = float(item["rrf_score"])
        return sorted(pool.values(), key=lambda item: item["score"], reverse=True)[:limit]

    @staticmethod
    def _lexical_score(terms: list[str], text: str) -> int:
        haystack = text.lower()
        return sum(haystack.count(term) for term in terms)

    def search_documents(
        self,
        query: str,
        collection_ids: list[str] | tuple[str, ...] | None = None,
        authorization_scope: AuthorizationScope | None = None,
    ) -> list[dict]:
        results = self.search(
            query,
            source_types=["document"],
            collection_ids=collection_ids,
            authorization_scope=authorization_scope,
        )
        return [
            {**item, "document_id": item["source_id"]}
            for item in results
        ]

    def search_wiki(
        self, query: str, authorization_scope: AuthorizationScope | None = None
    ) -> list[dict]:
        return self.search(
            query,
            source_types=["wiki"],
            authorization_scope=authorization_scope,
        )

    def get_wiki_page(
        self, path: str, authorization_scope: AuthorizationScope | None = None
    ) -> dict:
        with Session(self.engine) as session:
            statement = select(WikiPage).where(
                WikiPage.path == path,
                WikiPage.status == "published",
            )
            if authorization_scope is not None:
                if not authorization_scope.space_ids:
                    raise FileNotFoundError(path)
                statement = statement.where(
                    col(WikiPage.space_id).in_(authorization_scope.space_ids)
                )
            page = session.exec(statement).first()
        if page is None:
            raise FileNotFoundError(path)
        return {
            "source_type": "wiki",
            "path": page.path,
            "title": page.title,
            "content": page.content,
            "sources": json.loads(page.sources_json),
        }
