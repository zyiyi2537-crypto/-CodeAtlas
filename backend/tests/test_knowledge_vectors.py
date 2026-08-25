from __future__ import annotations

from pathlib import Path

from codeatlas.embeddings import EmbeddingClient
from codeatlas.knowledge_search import KnowledgeSearch
from codeatlas.models import DocumentChunkRecord, WikiPage
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
        "_document_rows",
        lambda _collections: [
            DocumentChunkRecord(
                id="doc-lexical",
                document_id="doc-1",
                collection_id="collection-1",
                title="Deployment",
                section="Nginx",
                page=2,
                structure_type="section",
                metadata_json="{}",
                content="Configure the reverse proxy.",
            )
        ],
    )
    monkeypatch.setattr(
        search,
        "_wiki_rows",
        lambda: [
            WikiPage(
                id="wiki-1",
                path="operations/backup.md",
                title="Backup",
                content="Back up MySQL and Chroma.",
                sources_json='["document://guide"]',
                created_by="admin",
            )
        ],
    )
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