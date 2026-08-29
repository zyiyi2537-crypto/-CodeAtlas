from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from codeatlas.chat import ChatService
from codeatlas.models import Repository, User
from tests.conftest import login_admin


def make_ready_repository(
    application, tmp_path: Path, creator: User, name: str = "demo"
) -> Repository:
    checkout = tmp_path / name
    (checkout / "src").mkdir(parents=True)
    (checkout / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (checkout / "README.md").write_text("# demo\n", encoding="utf-8")
    with Session(application.state.engine) as session:
        repo = Repository(
            name=name,
            git_url="https://github.com/org/demo.git",
            visibility="public",
            local_path=str(checkout),
            status="ready",
            active_generation_id="gen-1",
            chunk_count=2,
            last_commit="abc123",
            created_by=creator.id,
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)
        return repo


def test_chat_status_disabled_without_provider(client: TestClient) -> None:
    response = client.get("/api/v1/chat/status")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "model": "kimi-for-coding"}


def test_chat_unavailable_without_provider(client: TestClient, admin: User) -> None:
    login_admin(client)
    response = client.post("/api/v1/chat", json={"question": "入口在哪里？"})
    assert response.status_code == 503


def test_chat_answers_with_citations(
    client: TestClient,
    application,
    admin: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login_admin(client)
    make_ready_repository(application, tmp_path, admin)

    class FakeChat:
        enabled = True

        def __init__(self, *_args, **_kwargs):
            pass

        def ask(self, question, user, repository_ids=None, history=None):
            return {
                "answer": "入口在 src/app.py 的 main 函数 [1]。",
                "citations": [
                    {
                        "repo": "demo",
                        "path": "src/app.py",
                        "symbol": "main",
                        "start_line": 1,
                        "end_line": 2,
                    }
                ],
            }

    monkeypatch.setattr("codeatlas.api.ChatService", FakeChat)
    response = client.post(
        "/api/v1/chat",
        json={"question": "入口在哪里？", "history": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "main" in payload["answer"]
    assert payload["citations"][0]["path"] == "src/app.py"


def test_chat_uses_unified_document_and_wiki_evidence(settings, monkeypatch) -> None:
    class UnifiedRetriever:
        def search_knowledge(self, *_args, **_kwargs):
            return [
                {
                    "source_type": "document",
                    "source_id": "doc-1",
                    "title": "Deployment guide",
                    "section": "Nginx",
                    "page": 3,
                    "content": "Configure the reverse proxy.",
                    "score": 0.9,
                    "external_provider": "notion",
                    "external_source_id": "source-1",
                    "external_id": "page-1",
                    "source_url": "https://notion.so/page-1",
                }
            ]

    provider = type(
        "Provider",
        (),
        {"base_url": "https://llm.example/v1", "api_key": "test", "model": "test"},
    )()
    service = ChatService(settings, UnifiedRetriever(), provider)
    monkeypatch.setattr(service, "_complete", lambda messages: messages[-1]["content"])

    result = service.ask("怎么部署？", None)

    assert "source=document" in result["answer"]
    assert result["citations"][0] == {
        "source_type": "document",
        "source_id": "doc-1",
        "title": "Deployment guide",
        "section": "Nginx",
        "page": 3,
        "path": "",
        "repo": "",
        "symbol": "",
        "start_line": 0,
        "end_line": 0,
        "external_provider": "notion",
        "external_source_id": "source-1",
        "external_id": "page-1",
        "source_url": "https://notion.so/page-1",
        "structure_type": "",
        "sheet": "",
        "row_start": None,
        "row_end": None,
        "slide": None,
        "sources": [],
    }


def test_chat_context_and_citation_include_spreadsheet_coordinates(
    settings, monkeypatch
) -> None:
    class StructuredRetriever:
        def search_knowledge(self, *_args, **_kwargs):
            return [
                {
                    "source_type": "document",
                    "source_id": "budget-doc",
                    "title": "预算与SLA",
                    "section": "SLA矩阵",
                    "content": "订单创建接口P95目标为800ms。",
                    "score": 0.8,
                    "structure_type": "table",
                    "sheet": "SLA矩阵",
                    "row_start": 17,
                    "row_end": 17,
                    "slide": None,
                    "sources": [],
                }
            ]

    provider = type(
        "Provider",
        (),
        {"base_url": "https://llm.example/v1", "api_key": "test", "model": "test"},
    )()
    service = ChatService(settings, StructuredRetriever(), provider)
    monkeypatch.setattr(service, "_complete", lambda messages: messages[-1]["content"])

    result = service.ask("订单SLA是多少？", None)

    assert "sheet=SLA矩阵 rows=17-17" in result["answer"]
    assert result["citations"][0]["structure_type"] == "table"
    assert result["citations"][0]["sheet"] == "SLA矩阵"
    assert result["citations"][0]["row_start"] == 17


def test_chat_does_not_send_wiki_source_identifiers_to_external_llm(
    settings, monkeypatch
) -> None:
    private_source = "https://internal.example/wiki?signature=private-token"

    class WikiRetriever:
        def search_knowledge(self, *_args, **_kwargs):
            return [
                {
                    "source_type": "wiki",
                    "source_id": "wiki-1",
                    "title": "Current baseline",
                    "section": "Risk",
                    "content": "Kafka backlog is above target.",
                    "sources": [private_source],
                    "score": 0.8,
                }
            ]

    provider = type(
        "Provider",
        (),
        {"base_url": "https://llm.example/v1", "api_key": "test", "model": "test"},
    )()
    service = ChatService(settings, WikiRetriever(), provider)
    monkeypatch.setattr(service, "_complete", lambda messages: messages[-1]["content"])

    result = service.ask("当前风险是什么？", None)

    assert private_source not in result["answer"]
    assert result["citations"][0]["sources"] == [private_source]


def test_repository_tree_lists_entries(
    client: TestClient, application, admin: User, tmp_path: Path
) -> None:
    login_admin(client)
    repo = make_ready_repository(application, tmp_path, admin)

    root = client.get(f"/api/v1/repositories/{repo.id}/tree")
    assert root.status_code == 200
    names = {entry["name"] for entry in root.json()["entries"]}
    assert names == {"src", "README.md"}

    nested = client.get(f"/api/v1/repositories/{repo.id}/tree", params={"path": "src"})
    assert nested.status_code == 200
    entries = nested.json()["entries"]
    assert entries[0]["name"] == "app.py"
    assert entries[0]["type"] == "file"

    escape = client.get(
        f"/api/v1/repositories/{repo.id}/tree", params={"path": "../../.."}
    )
    assert escape.status_code == 422

    missing = client.get(
        f"/api/v1/repositories/{repo.id}/tree", params={"path": "nope"}
    )
    assert missing.status_code == 404

    forbidden = client.get("/api/v1/repositories/unknown-repo/tree")
    assert forbidden.status_code == 403


def test_tree_respects_private_visibility(
    client: TestClient, application, admin: User, tmp_path: Path
) -> None:
    repo = make_ready_repository(application, tmp_path, admin, name="private-demo")
    with Session(application.state.engine) as session:
        stored = session.get(Repository, repo.id)
        assert stored is not None
        stored.visibility = "private"
        session.add(stored)
        session.commit()
    response = client.get(f"/api/v1/repositories/{repo.id}/tree")
    assert response.status_code == 403


def test_stats_endpoint(client: TestClient, application, admin: User, tmp_path: Path) -> None:
    login_admin(client)
    make_ready_repository(application, tmp_path, admin)
    response = client.get("/api/v1/stats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["repository_count"] == 1
    assert payload["ready_count"] == 1
    assert payload["chunk_total"] == 2
    assert payload["languages"] == []
