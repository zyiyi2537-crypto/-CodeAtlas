from __future__ import annotations

from pathlib import Path

from codeatlas.embeddings import EmbeddingClient
from codeatlas.knowledge_search import KnowledgeSearch
from codeatlas.models import WikiPage
from codeatlas.settings import Settings
from codeatlas.vector_store import KnowledgeVectorChunk, VectorStore


def vector_settings(tmp_path: Path, dimension: int = 128) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        project_root=tmp_path,
        data_dir=data_dir,
        database_url="mysql+pymysql://unused@127.0.0.1/unused",
        repositories_dir=data_dir / "repositories",
        chroma_path=data_dir / "chroma",
        environment="test",
        public_origin="http://testserver",
        cookie_secure=False,
        allow_anonymous_search=True,
        allow_anonymous_chat=False,
        build_revision="test",
        allowed_git_hosts=("github.com",),
        mcp_allowed_hosts=("testserver",),
        embedding_mode="hash",
        embedding_base_url="",
        embedding_api_key="",
        embedding_model="hash-embedding-v1",
        embedding_dimension=dimension,
        llm_base_url="",
        llm_api_key="",
        llm_model="test",
        max_repository_mb=20,
        max_source_files=100,
        git_timeout_seconds=30,
    )


def test_embedding_profiles_use_isolated_collections(tmp_path: Path) -> None:
    first = VectorStore(vector_settings(tmp_path), namespace="profile-a")
    second = VectorStore(vector_settings(tmp_path, 256), namespace="profile-b")

    assert first.collection.name != second.collection.name
    assert first.collection.metadata["embedding_dimension"] == 128
    assert second.collection.metadata["embedding_dimension"] == 256


def test_knowledge_chunks_share_profile_collection_with_source_filters(tmp_path: Path) -> None:
    settings = vector_settings(tmp_path)
    store = VectorStore(settings, namespace="profile-a")
    embedder = EmbeddingClient(settings)
    chunks = [
        KnowledgeVectorChunk(
            id="document-1",
            content="Document: Guide\nSection: Deployment\n\nDeploy with Nginx.",
            metadata={
                "source_type": "document",
                "source_id": "doc-1",
                "collection_id": "collection-1",
                "title": "Guide",
                "section": "Deployment",
                "page": 2,
            },
        ),
        KnowledgeVectorChunk(
            id="wiki-1",
            content="Wiki: Operations\nSection: Backup\n\nBack up MySQL and Chroma.",
            metadata={
                "source_type": "wiki",
                "source_id": "wiki-1",
                "collection_id": "",
                "title": "Operations",
                "section": "Backup",
                "page": 0,
            },
        ),
    ]

    store.add_knowledge(chunks, embedder)
    document_results = store.search_knowledge(
        embedder.embed(["deploy nginx"])[0], source_types=["document"], limit=5
    )

    assert [item["metadata"]["source_type"] for item in document_results] == ["document"]
    assert document_results[0]["metadata"]["source_id"] == "doc-1"


class FakeEngine:
    pass


def test_knowledge_search_fuses_vector_and_lexical_results(tmp_path: Path, monkeypatch) -> None:
    settings = vector_settings(tmp_path)
    search = KnowledgeSearch(FakeEngine(), settings)
    monkeypatch.setattr(
        search,
        "_lexical_candidates",
        lambda *_args, **_kwargs: [
            {
                "id": "doc-lexical",
                "source_type": "document",
                "source_id": "doc-1",
                "collection_id": "collection-1",
                "title": "Deployment",
                "section": "Nginx",
                "page": 2,
                "content": "Configure the reverse proxy.",
                "structure_type": "section",
                "sheet": "",
                "row_start": None,
                "row_end": None,
                "slide": None,
                "sources": [],
                "lexical_score": 1.0,
            }
        ],
    )
    monkeypatch.setattr(search, "_indexed_document_ids", lambda: {"doc-1"})
    monkeypatch.setattr(
        search.vector_store,
        "search_knowledge",
        lambda *_args, **_kwargs: [
            {
                "id": "doc-lexical",
                "document": "Configure the reverse proxy.",
                "metadata": {
                    "source_type": "document",
                    "source_id": "doc-1",
                    "collection_id": "collection-1",
                    "title": "Deployment",
                    "section": "Nginx",
                    "page": 2,
                },
                "vector_score": 0.9,
            }
        ],
    )

    results = search.search("reverse proxy", source_types=["document", "wiki"])

    assert results[0]["source_type"] == "document"
    assert results[0]["retrieval"] == "hybrid"
    assert results[0]["page"] == 2


def test_knowledge_search_uses_rrf_and_preserves_structural_citations(
    tmp_path: Path, monkeypatch
) -> None:
    settings = vector_settings(tmp_path)
    search = KnowledgeSearch(FakeEngine(), settings)
    lexical = [
        {
            "id": "sheet-row-17",
            "source_type": "document",
            "source_id": "doc-budget",
            "collection_id": "atlas",
            "title": "预算与SLA",
            "section": "SLA矩阵",
            "page": None,
            "content": "订单创建接口 P95 目标为 800ms。",
            "structure_type": "table",
            "sheet": "SLA矩阵",
            "row_start": 17,
            "row_end": 17,
            "slide": None,
            "sources": [],
            "lexical_score": 9.5,
        }
    ]
    monkeypatch.setattr(search, "_lexical_candidates", lambda *_args, **_kwargs: lexical)
    monkeypatch.setattr(search, "_indexed_document_ids", lambda: {"doc-budget"})
    monkeypatch.setattr(
        search.vector_store,
        "search_knowledge",
        lambda *_args, **_kwargs: [
            {
                "id": "sheet-row-17",
                "document": "订单创建接口 P95 目标为 800ms。",
                "metadata": {
                    "source_type": "document",
                    "source_id": "doc-budget",
                    "collection_id": "atlas",
                    "title": "预算与SLA",
                    "section": "SLA矩阵",
                    "structure_type": "table",
                    "sheet": "SLA矩阵",
                    "row_start": 17,
                    "row_end": 17,
                },
                "vector_score": 0.91,
            }
        ],
    )

    results = search.search("订单创建 P95", source_types=["document"])

    assert results[0]["retrieval"] == "hybrid"
    assert results[0]["rrf_score"] > 0
    assert results[0]["structure_type"] == "table"
    assert results[0]["sheet"] == "SLA矩阵"
    assert results[0]["row_start"] == 17


def test_wiki_lexical_candidates_align_with_markdown_section_vector_ids(
    tmp_path: Path, monkeypatch
) -> None:
    settings = vector_settings(tmp_path)
    search = KnowledgeSearch(FakeEngine(), settings)
    page = WikiPage(
        id="wiki-atlas",
        path="atlas/operations.md",
        title="Atlas运维知识",
        content="# Atlas运维知识\n\n## 蓝绿切换\n\n切换窗口为每周三 22:30。",
        sources_json='["document://runbook#section=蓝绿切换"]',
        created_by="admin",
    )
    monkeypatch.setattr(search, "_wiki_fulltext_pages", lambda *_args: [page])

    candidates = search._wiki_lexical_candidates(["蓝绿", "切换"], limit=10)

    section = next(item for item in candidates if "蓝绿切换" in item["section"])
    assert section["id"] == "wiki:wiki-atlas:1"
    assert section["sources"] == ["document://runbook#section=蓝绿切换"]
    assert section["structure_type"] == "section"


def test_knowledge_search_drops_zero_similarity_vector_only_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    settings = vector_settings(tmp_path)
    search = KnowledgeSearch(FakeEngine(), settings)
    monkeypatch.setattr(search, "_lexical_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(search, "_indexed_document_ids", lambda: {"unrelated-doc"})
    monkeypatch.setattr(
        search.vector_store,
        "search_knowledge",
        lambda *_args, **_kwargs: [
            {
                "id": "zero-similarity",
                "document": "Unrelated evidence.",
                "metadata": {
                    "source_type": "document",
                    "source_id": "unrelated-doc",
                    "collection_id": "atlas",
                    "title": "Unrelated",
                    "section": "Other",
                },
                "vector_score": 0.0,
            }
        ],
    )

    assert search.search("missing fact", source_types=["document"]) == []


def test_document_lexical_scores_cannot_starve_the_wiki_lexical_lane(
    tmp_path: Path, monkeypatch
) -> None:
    settings = vector_settings(tmp_path)
    search = KnowledgeSearch(FakeEngine(), settings)
    documents = [
        {
            "id": f"document-{index}",
            "source_type": "document",
            "source_id": f"doc-{index}",
            "collection_id": "atlas",
            "title": "Document",
            "section": "Section",
            "page": None,
            "content": "document evidence",
            "structure_type": "section",
            "sheet": "",
            "row_start": None,
            "row_end": None,
            "slide": None,
            "sources": [],
            "lexical_score": float(1000 - index),
        }
        for index in range(30)
    ]
    wiki = {
        "id": "wiki-first",
        "source_type": "wiki",
        "source_id": "wiki-1",
        "collection_id": "",
        "title": "Wiki",
        "section": "Decision",
        "page": None,
        "path": "atlas/decision.md",
        "content": "wiki evidence",
        "structure_type": "section",
        "sheet": "",
        "row_start": None,
        "row_end": None,
        "slide": None,
        "sources": ["document://decision"],
        "lexical_score": 1.0,
    }
    monkeypatch.setattr(
        search, "_document_lexical_candidates", lambda *_args, **_kwargs: documents
    )
    monkeypatch.setattr(
        search, "_wiki_lexical_candidates", lambda *_args, **_kwargs: [wiki]
    )
    monkeypatch.setattr(search, "_indexed_document_ids", lambda: {f"doc-{i}" for i in range(30)})
    monkeypatch.setattr(search.vector_store, "search_knowledge", lambda *_args, **_kwargs: [])

    results = search.search(
        "shared evidence",
        source_types=["document", "wiki"],
        limit=10,
    )

    assert any(item["id"] == "wiki-first" for item in results)
    assert next(item for item in results if item["id"] == "wiki-first")["source_rank"] == 1
