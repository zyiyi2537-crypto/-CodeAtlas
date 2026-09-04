from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from codeatlas import mcp_server
from codeatlas.authorization import resolve_authorization_scope
from codeatlas.conventions import find_company_conventions
from codeatlas.mcp_server import McpIdentity
from codeatlas.models import (
    DEFAULT_SPACE_ID,
    CompanyConvention,
    DocumentCollection,
    KnowledgeSpace,
    Repository,
    RepositoryAccess,
    SpaceGrant,
    User,
)
from codeatlas.security import hash_password


def _add_member(application, email: str = "member@example.com") -> User:
    member = User(
        email=email,
        display_name="Member",
        password_hash=hash_password("member password 1234"),
        role="member",
    )
    with Session(application.state.engine) as session:
        session.add(member)
        session.commit()
        session.refresh(member)
    return member


def _login_member(client: TestClient, email: str = "member@example.com") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "member password 1234"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


def test_restricted_space_changes_all_resolved_resource_boundaries(
    application,
    admin: User,
) -> None:
    member = _add_member(application)
    with Session(application.state.engine) as session:
        restricted = KnowledgeSpace(
            workspace_id="default-workspace",
            name="Restricted engineering",
            visibility="restricted",
        )
        session.add(restricted)
        session.flush()
        default_repository = Repository(
            name="default-public",
            git_url="https://github.com/example/default-public.git",
            visibility="public",
            created_by=admin.id,
        )
        restricted_repository = Repository(
            name="restricted-public",
            git_url="https://github.com/example/restricted-public.git",
            visibility="public",
            space_id=restricted.id,
            created_by=admin.id,
        )
        restricted_collection = DocumentCollection(
            name="Restricted documents",
            space_id=restricted.id,
            created_by=admin.id,
        )
        session.add_all(
            [default_repository, restricted_repository, restricted_collection]
        )
        session.commit()
        default_repository_id = default_repository.id
        restricted_id = restricted.id
        restricted_repository_id = restricted_repository.id
        restricted_collection_id = restricted_collection.id

    with Session(application.state.engine) as session:
        stored_member = session.get(User, member.id)
        assert stored_member is not None
        before = resolve_authorization_scope(session, stored_member)
        assert before.space_ids == (DEFAULT_SPACE_ID,)
        assert restricted_repository_id not in before.repository_ids
        assert restricted_collection_id not in before.collection_ids

        session.add(
            SpaceGrant(space_id=restricted_id, user_id=member.id, role="viewer")
        )
        session.commit()

    with Session(application.state.engine) as session:
        stored_member = session.get(User, member.id)
        assert stored_member is not None
        after = resolve_authorization_scope(session, stored_member)
        assert set(after.space_ids) == {DEFAULT_SPACE_ID, restricted_id}
        assert restricted_repository_id in after.repository_ids
        assert restricted_collection_id in after.collection_ids
        assert after.permits_space(restricted_id, "read")
        assert not after.permits_space(restricted_id, "edit")

        restricted_grant = session.exec(
            select(SpaceGrant).where(
                SpaceGrant.space_id == restricted_id,
                SpaceGrant.user_id == member.id,
            )
        ).one()
        restricted_grant.role = "editor"
        session.add(restricted_grant)
        session.add(
            SpaceGrant(
                space_id=DEFAULT_SPACE_ID,
                user_id=member.id,
                role="viewer",
            )
        )
        session.commit()

    with Session(application.state.engine) as session:
        stored_member = session.get(User, member.id)
        assert stored_member is not None
        mixed_roles = resolve_authorization_scope(session, stored_member)
        assert mixed_roles.permits_repository(restricted_repository_id, "edit")
        assert not mixed_roles.permits_repository(default_repository_id, "edit")


def test_member_can_only_create_token_for_current_access_scope(
    client: TestClient,
    application,
    admin: User,
) -> None:
    member = _add_member(application)
    with Session(application.state.engine) as session:
        granted_repository = Repository(
            name="member-private",
            git_url="https://github.com/example/member-private.git",
            visibility="private",
            created_by=admin.id,
        )
        denied_repository = Repository(
            name="member-denied",
            git_url="https://github.com/example/member-denied.git",
            visibility="private",
            created_by=admin.id,
        )
        session.add_all([granted_repository, denied_repository])
        session.flush()
        session.add(
            RepositoryAccess(
                repository_id=granted_repository.id,
                user_id=member.id,
            )
        )
        session.commit()
        granted_repository_id = granted_repository.id
        denied_repository_id = denied_repository.id

    csrf = _login_member(client)
    headers = {"X-CSRF-Token": csrf}
    created = client.post(
        "/api/v1/tokens",
        headers=headers,
        json={
            "name": "My Codex",
            "scopes": ["status", "search", "read"],
            "repository_ids": [granted_repository_id],
        },
    )
    assert created.status_code == 201
    assert created.json()["repository_ids"] == [granted_repository_id]
    assert created.json()["space_ids"] == [DEFAULT_SPACE_ID]
    assert created.json()["token"].startswith("cat_")

    denied = client.post(
        "/api/v1/tokens",
        headers=headers,
        json={
            "name": "Too broad",
            "scopes": ["read"],
            "repository_ids": [denied_repository_id],
        },
    )
    assert denied.status_code == 422


def test_company_conventions_require_confirmed_status_and_repository_access(
    application,
    admin: User,
    monkeypatch,
) -> None:
    member = _add_member(application)
    with Session(application.state.engine) as session:
        public_repository = Repository(
            name="convention-public",
            git_url="https://github.com/example/convention-public.git",
            visibility="public",
            created_by=admin.id,
        )
        private_repository = Repository(
            name="convention-private",
            git_url="https://github.com/example/convention-private.git",
            visibility="private",
            created_by=admin.id,
        )
        session.add_all([public_repository, private_repository])
        session.flush()

        def citation(repository: Repository) -> str:
            return json.dumps(
                [
                    {
                        "repository_id": repository.id,
                        "commit": "a" * 40,
                        "path": "src/example.ts",
                        "symbol": "example",
                        "start_line": 1,
                        "end_line": 5,
                    }
                ]
            )

        session.add_all(
            [
                CompanyConvention(
                    title="Confirmed public rule",
                    category="naming",
                    language="typescript",
                    rule="Use descriptive component names.",
                    citations_json=citation(public_repository),
                    status="confirmed",
                    created_by=admin.id,
                ),
                CompanyConvention(
                    title="Draft public rule",
                    category="naming",
                    language="typescript",
                    rule="This rule is not reviewed.",
                    citations_json=citation(public_repository),
                    status="draft",
                    created_by=admin.id,
                ),
                CompanyConvention(
                    title="Confirmed private rule",
                    category="security",
                    language="typescript",
                    rule="This source is outside the member scope.",
                    citations_json=citation(private_repository),
                    status="confirmed",
                    created_by=admin.id,
                ),
            ]
        )
        session.commit()

    with Session(application.state.engine) as session:
        stored_member = session.get(User, member.id)
        assert stored_member is not None
        scope = resolve_authorization_scope(session, stored_member)
        direct_results = find_company_conventions(
            session,
            scope,
            language="typescript",
        )
    assert [item["title"] for item in direct_results] == ["Confirmed public rule"]

    registered: dict[str, object] = {}

    class FakeFastMCP:
        def __init__(self, *_args, **_kwargs):
            pass

        def tool(self, **_kwargs):
            def register(function):
                registered[function.__name__] = function
                return function

            return register

        def streamable_http_app(self):
            return object()

    class Retriever:
        def __init__(self):
            self.vector_store = type("Store", (), {"count": lambda self: 0})()

    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)
    mcp_server.build_mcp(
        type(
            "Settings",
            (),
            {
                "mcp_allowed_hosts": ("localhost",),
                "public_origin": "https://example.com",
            },
        )(),
        application.state.engine,
        Retriever(),
        default_identity=McpIdentity(
            scopes=frozenset({"read"}),
            repository_ids=scope.repository_ids,
            space_ids=scope.space_ids,
            collection_ids=scope.collection_ids,
            actor_user_id=member.id,
        ),
        knowledge_search=object(),
    )
    get_conventions = registered["get_company_conventions"]
    assert callable(get_conventions)
    assert [item["title"] for item in get_conventions(language="typescript")] == [
        "Confirmed public rule"
    ]
