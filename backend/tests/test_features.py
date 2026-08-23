from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

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
