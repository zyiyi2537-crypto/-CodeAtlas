from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codeatlas import api
from codeatlas.authorization import AuthorizationScope
from codeatlas.chat import ChatService
from codeatlas.embeddings import EmbeddingClient, EmbeddingUnavailableError
from codeatlas.knowledge_search import KnowledgeSearch
from codeatlas.models import Repository
from codeatlas.retrieval import CodeRetriever, RevisionUnavailableError
from codeatlas.settings import Settings


def local_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEATLAS_DATA_DIR", str(tmp_path))
    return replace(
        Settings.load(), embedding_mode="openai", embedding_base_url="https://model.example/v1",
        embedding_api_key="test-only", embedding_dimension=64,
    )


@pytest.mark.parametrize("failure", ["timeout", 408, 429, 500, 503])
@pytest.mark.parametrize("mode", ["openai", "tencent_multimodal"])
def test_query_embedding_has_short_budget_and_only_one_attempt(
    monkeypatch, tmp_path, failure, mode,
):
    settings = replace(local_settings(monkeypatch, tmp_path), embedding_mode=mode)
    requests = []

    def post(url, **kwargs):
        requests.append(kwargs)
        request = httpx.Request("POST", url)
        if failure == "timeout":
            raise httpx.ReadTimeout("unavailable", request=request)
        return httpx.Response(failure, request=request)

    monkeypatch.setattr("codeatlas.embeddings.httpx.post", post)
    with pytest.raises(EmbeddingUnavailableError):
        EmbeddingClient(settings).embed_query("authorized query")
    assert len(requests) == 1
    assert requests[0]["timeout"] == 5


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_query_embedding_does_not_mask_configuration_or_authentication_errors(
    monkeypatch, tmp_path, status_code,
):
    settings = local_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "codeatlas.embeddings.httpx.post",
        lambda url, **_kwargs: httpx.Response(
            status_code, request=httpx.Request("POST", url),
        ),
    )
    with pytest.raises(httpx.HTTPStatusError):
        EmbeddingClient(settings).embed_query("query")


def unavailable(*_args, **_kwargs):
    raise EmbeddingUnavailableError("unavailable")


@pytest.mark.parametrize("error_type", [httpx.UnsupportedProtocol, httpx.LocalProtocolError])
def test_query_embedding_does_not_mask_invalid_transport_configuration(
    monkeypatch, tmp_path, error_type,
):
    settings = local_settings(monkeypatch, tmp_path)

    def invalid_request(*_args, **_kwargs):
        raise error_type("invalid local configuration")

    monkeypatch.setattr("codeatlas.embeddings.httpx.post", invalid_request)
    with pytest.raises(error_type):
        EmbeddingClient(settings).embed_query("query")


def test_code_fallback_preserves_generation_and_filters(monkeypatch, tmp_path):
    settings = local_settings(monkeypatch, tmp_path)
    retriever = object.__new__(CodeRetriever)
    repository = Repository(
        id="repo-1", name="test", git_url="https://github.com/org/test.git",
        created_by="user-1", active_generation_id="generation-1", status="ready",
    )
    monkeypatch.setattr(retriever, "allowed_repositories", lambda *_args: [repository])
    monkeypatch.setattr(retriever, "_current_embedding_context", lambda: (settings, "profile"))
    calls = []

    def lexical(query, generations, limit, languages, path):
        calls.append((query, generations, limit, languages, path))
        return [{
            "id": "chunk-1", "document": "def search(): pass", "lexical_score": 1.0,
            "metadata": {
                "repo": "repo-1", "generation_id": "generation-1", "path": "src/main.py",
                "language": "python", "symbol": "search", "start_line": 1, "end_line": 1,
            },
        }]

    monkeypatch.setattr(retriever, "_lexical_candidates", lexical)
    monkeypatch.setattr(EmbeddingClient, "embed_query", unavailable)
    results = retriever.search("search", languages=["python"], path_prefix="src/")
    assert calls == [("search", ["generation-1"], 50, ["python"], "src/")]
    assert results[0]["repo"] == "repo-1"
    assert results[0]["retrieval"] == "lexical"
    assert results[0]["degraded"] is True


def test_knowledge_fallback_passes_authorization_before_provider_call(monkeypatch):
    search = object.__new__(KnowledgeSearch)
    search._context = SimpleNamespace(embedder=SimpleNamespace(embed_query=unavailable))
    scope = AuthorizationScope(
        actor_user_id="user-1", space_ids=("space-1",), repository_ids=(),
        collection_ids=("collection-1",), actions=frozenset({"read"}),
    )
    calls = []

    def lexical(_terms, wanted, collections, _limit, spaces):
        calls.append((wanted, collections, spaces))
        return [{
            "id": "doc-chunk", "source_type": "document", "source_id": "doc-1",
            "collection_id": "collection-1", "content": "authorized content",
            "lexical_score": 1.0, "lexical_rank": 1,
        }]

    monkeypatch.setattr(search, "_lexical_candidates", lexical)
    monkeypatch.setattr(search, "_indexed_document_ids", lambda: {"doc-1"})
    result = search.search("query", source_types=["document"], authorization_scope=scope)
    assert calls == [(["document"], ("collection-1",), ("space-1",))]
    assert result[0]["degradation_reason"] == "embedding_unavailable"
    assert result[0]["retrieval"] == "lexical"
    calls.clear()
    assert search.search(
        "query", source_types=["document"], collection_ids=["forbidden"],
        authorization_scope=scope,
    ) == []
    assert calls == []


def test_file_revision_check_rejects_stale_commit_and_preserves_permission_order(
    monkeypatch, tmp_path,
):
    (tmp_path / "main.py").write_text("print('new revision')", encoding="utf-8")
    repository = Repository(
        id="repo-1", name="test", git_url="https://github.com/org/test.git",
        created_by="user-1", local_path=str(tmp_path), last_commit="b" * 40,
    )
    retriever = object.__new__(CodeRetriever)
    monkeypatch.setattr(retriever, "allowed_repositories", lambda *_args, **_kwargs: [repository])
    with pytest.raises(RevisionUnavailableError):
        retriever.get_file("repo-1", "main.py", None, commit="a" * 40)
    result = retriever.get_file("repo-1", "main.py", None, commit="b" * 40)
    assert result["commit"] == "b" * 40
    assert "new revision" in result["content"]
    monkeypatch.setattr(retriever, "allowed_repositories", lambda *_args, **_kwargs: [])
    with pytest.raises(PermissionError):
        retriever.get_file("repo-1", "main.py", None, commit="a" * 40)


def test_chat_citations_only_include_evidence_sent_to_model(monkeypatch, tmp_path):
    settings = local_settings(monkeypatch, tmp_path)
    evidence = [
        {"source_type": "code", "repo": "large", "snippet": "x" * 13_000},
        {"source_type": "code", "repo": "small", "snippet": "def main(): pass",
         "path": "main.py", "commit": "a" * 40},
    ]
    provider = SimpleNamespace(base_url="https://model.example/v1", api_key="test", model="test")
    service = ChatService(settings, SimpleNamespace(search_knowledge=lambda *_a, **_kw: evidence),
                          provider)
    prompts = []

    def complete(messages):
        prompts.append(messages[-1]["content"])
        return "main [1]"

    monkeypatch.setattr(service, "_complete", complete)
    result = service.ask("entrypoint", None)
    assert len(result["citations"]) == 1
    assert result["citations"][0]["commit"] == "a" * 40
    assert result["citations"][0]["repo"] == "small"
    assert "[1]" in prompts[0]
    assert "[2]" not in prompts[0]
    assert "x" * 100 not in prompts[0]


def test_file_api_returns_conflict_for_stale_citation(monkeypatch):
    application = FastAPI()
    application.include_router(api.router)
    application.state.settings = SimpleNamespace(allow_anonymous_search=True)
    seen = []

    def get_file(*_args, **kwargs):
        seen.append(kwargs)
        raise RevisionUnavailableError("Requested code revision is no longer active")

    application.state.retriever = SimpleNamespace(get_file=get_file)
    scope = object()
    monkeypatch.setattr(api, "database", lambda _request: nullcontext(None))
    monkeypatch.setattr(api, "resolve_identity", lambda *_args: None)
    monkeypatch.setattr(api, "authorization_scope", lambda *_args, **_kwargs: scope)
    with TestClient(application) as client:
        response = client.get(
            "/api/v1/repositories/repo-1/file", params={"path": "main.py", "commit": "a" * 40},
        )
    assert response.status_code == 409
    assert seen[0]["commit"] == "a" * 40
    assert seen[0]["authorization_scope"] is scope
