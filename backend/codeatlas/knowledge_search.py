from __future__ import annotations

import json

from sqlmodel import Session, col, select

from .models import DocumentChunkRecord, WikiPage


class KnowledgeSearch:
    """Searches project documents and source-tracked Wiki pages."""

    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _terms(query: str) -> list[str]:
        normalized = query.strip()
        if not normalized or len(normalized) > 500:
            raise ValueError("query must contain between 1 and 500 characters")
        return [term.lower() for term in normalized.split() if term.strip()]

    def search_documents(
        self, query: str, collection_ids: list[str] | tuple[str, ...] | None = None
    ) -> list[dict]:
        terms = self._terms(query)
        with Session(self.engine) as session:
            statement = select(DocumentChunkRecord)
            if collection_ids:
                statement = statement.where(
                    col(DocumentChunkRecord.collection_id).in_(collection_ids)
                )
            rows = session.exec(statement).all()

        results = []
        for row in rows:
            haystack = f"{row.title} {row.section} {row.content}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                results.append(
                    {
                        "source_type": "document",
                        "document_id": row.document_id,
                        "collection_id": row.collection_id,
                        "title": row.title,
                        "section": row.section,
                        "page": row.page,
                        "content": row.content,
                        "score": score,
                    }
                )
        return sorted(results, key=lambda item: int(str(item["score"])), reverse=True)[:10]

    def search_wiki(self, query: str) -> list[dict]:
        terms = self._terms(query)
        with Session(self.engine) as session:
            pages = session.exec(select(WikiPage).where(WikiPage.status == "published")).all()

        results = []
        for page in pages:
            haystack = f"{page.title} {page.content}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                results.append(
                    {
                        "source_type": "wiki",
                        "path": page.path,
                        "title": page.title,
                        "content": page.content,
                        "sources": json.loads(page.sources_json),
                        "score": score,
                    }
                )
        return sorted(results, key=lambda item: int(str(item["score"])), reverse=True)[:10]

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
