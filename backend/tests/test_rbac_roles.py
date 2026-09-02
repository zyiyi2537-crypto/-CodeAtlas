from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from codeatlas import api, cli
from codeatlas.mcp_server import resolve_token_identity
from codeatlas.member_lifecycle_lock import member_lifecycle_lock
from codeatlas.models import (
    ApiToken,
    AuditEvent,
    ChatMessage,
    ChatSession,
    Repository,
    RepositoryAccess,
    User,
    UserMemory,
    UserSession,
)
from codeatlas.security import digest_secret, hash_password


def add_user(application, email: str, role: str, *, active: bool = True) -> User:
    user = User(
        email=email,
        display_name=email.split("@", 1)[0],
        password_hash=hash_password("correct horse battery staple"),
        role=role,
        is_active=active,
    )
    with Session(application.state.engine) as session:
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct horse battery staple"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


def test_owner_and_workspace_admin_have_administrative_access(
    client: TestClient, application
) -> None:
    add_user(application, "owner@example.com", "owner")
    add_user(application, "workspace-admin@example.com", "workspace_admin")
    add_user(application, "member@example.com", "member")

    login(client, "owner@example.com")
    owner_members = client.get("/api/v1/members")
    assert owner_members.status_code == 200
    assert {item["email"] for item in owner_members.json()} == {
        "owner@example.com",
        "workspace-admin@example.com",
        "member@example.com",
    }
    client.cookies.clear()

    login(client, "workspace-admin@example.com")
    admin_members = client.get("/api/v1/members")
    assert admin_members.status_code == 200
    assert {item["email"] for item in admin_members.json()} == {
        "workspace-admin@example.com",
        "member@example.com",
    }
    client.cookies.clear()

    login(client, "member@example.com")
    assert client.get("/api/v1/members").status_code == 403


def test_workspace_admin_can_manage_members_but_not_privileged_accounts(
    client: TestClient, application
) -> None:
    owner = add_user(application, "owner@example.com", "owner")
    actor = add_user(application, "actor@example.com", "workspace_admin")
    peer = add_user(application, "peer@example.com", "workspace_admin")
    member = add_user(application, "member@example.com", "member")
    csrf = login(client, actor.email)
    headers = {"X-CSRF-Token": csrf}

    with Session(application.state.engine) as session:
        repository = Repository(
            name="role-test",
            git_url="https://github.com/example/role-test.git",
            branch="main",
            visibility="private",
            created_by=actor.id,
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
        repository_id = repository.id

    created_member = client.post(
        "/api/v1/members",
        headers=headers,
        json={
            "email": "new-member@example.com",
            "display_name": "New member",
            "password": "another correct password",
            "role": "member",
        },
    )
    assert created_member.status_code == 201

    forbidden_create = client.post(
        "/api/v1/members",
        headers=headers,
        json={
            "email": "new-admin@example.com",
            "display_name": "New admin",
            "password": "another correct password",
            "role": "workspace_admin",
        },
    )
    assert forbidden_create.status_code == 403

    assert client.patch(
        f"/api/v1/members/{member.id}",
        headers=headers,
        json={"is_active": False},
    ).status_code == 200
    for target in (owner, peer):
        assert client.patch(
            f"/api/v1/members/{target.id}",
            headers=headers,
            json={"is_active": False},
        ).status_code == 403
        assert client.delete(
            f"/api/v1/members/{target.id}", headers=headers
        ).status_code == 403
        assert client.put(
            f"/api/v1/members/{target.id}/repositories/{repository_id}",
            headers=headers,
        ).status_code == 403


def test_owner_assigns_roles_and_cannot_remove_the_last_active_owner(
    client: TestClient, application
) -> None:
    owner = add_user(application, "owner@example.com", "owner")
    target = add_user(application, "target@example.com", "member")
    csrf = login(client, owner.email)
    headers = {"X-CSRF-Token": csrf}

    promoted = client.patch(
        f"/api/v1/members/{target.id}",
        headers=headers,
        json={"role": "workspace_admin"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "workspace_admin"

    assert client.patch(
        f"/api/v1/members/{owner.id}",
        headers=headers,
        json={"role": "workspace_admin"},
    ).status_code == 409
    assert client.patch(
        f"/api/v1/members/{owner.id}",
        headers=headers,
        json={"is_active": False},
    ).status_code == 409
    assert client.delete(
        f"/api/v1/members/{owner.id}", headers=headers
    ).status_code == 400

    with Session(application.state.engine) as session:
        stored_owner = session.get(User, owner.id)
        assert stored_owner is not None
        assert stored_owner.role == "owner"
        assert stored_owner.is_active is True


def test_role_bootstrap_sets_explicit_owner_and_all_others_to_workspace_admin(
    application,
) -> None:
    from codeatlas.roles import configure_single_workspace_roles

    owner = add_user(application, "admin@example.com", "admin")
    other = add_user(application, "other@example.com", "member")
    disabled = add_user(application, "disabled@example.com", "admin", active=False)
    password_hashes = {
        item.email: item.password_hash
        for item in (owner, other, disabled)
    }
    with Session(application.state.engine) as session:
        browser_session = UserSession(
            user_id=other.id,
            token_hash="c" * 64,
            csrf_token="csrf-preserved",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        token = ApiToken(
            name="preserved",
            token_prefix="cat_keep",
            token_hash="d" * 64,
            created_by=other.id,
        )
        repository = Repository(
            name="preserved-role-repository",
            git_url="https://github.com/example/preserved-role-repository.git",
            branch="main",
            visibility="private",
            created_by=other.id,
        )
        conversation = ChatSession(user_id=other.id, title="Preserved conversation")
        memory = UserMemory(
            user_id=other.id,
            kind="preference",
            content="Keep this memory",
            content_hash="e" * 64,
        )
        session.add_all([browser_session, token, repository, conversation, memory])
        session.flush()
        message = ChatMessage(
            session_id=conversation.id,
            user_id=other.id,
            role="user",
            sequence=1,
            content="Keep this message",
        )
        access = RepositoryAccess(repository_id=repository.id, user_id=disabled.id)
        session.add_all([message, access])
        session.commit()
        preserved_ids = {
            "browser_session": browser_session.id,
            "token": token.id,
            "repository": repository.id,
            "conversation": conversation.id,
            "message": message.id,
            "memory": memory.id,
            "access": access.id,
        }

    result = configure_single_workspace_roles(
        application.state.engine, "ADMIN@example.com"
    )

    assert result == {"owner": 1, "workspace_admin": 2}
    with Session(application.state.engine) as session:
        users = session.exec(select(User).order_by(User.email)).all()
        assert {user.email: user.role for user in users} == {
            "admin@example.com": "owner",
            "disabled@example.com": "workspace_admin",
            "other@example.com": "workspace_admin",
        }
        assert {user.email: user.is_active for user in users} == {
            "admin@example.com": True,
            "disabled@example.com": False,
            "other@example.com": True,
        }
        assert {user.email: user.password_hash for user in users} == password_hashes
        assert session.get(UserSession, preserved_ids["browser_session"]) is not None
        assert session.get(ApiToken, preserved_ids["token"]) is not None
        preserved_repository = session.get(Repository, preserved_ids["repository"])
        assert preserved_repository is not None
        assert preserved_repository.created_by == other.id
        preserved_conversation = session.get(ChatSession, preserved_ids["conversation"])
        assert preserved_conversation is not None
        assert preserved_conversation.user_id == other.id
        preserved_message = session.get(ChatMessage, preserved_ids["message"])
        assert preserved_message is not None
        assert preserved_message.user_id == other.id
        preserved_memory = session.get(UserMemory, preserved_ids["memory"])
        assert preserved_memory is not None
        assert preserved_memory.user_id == other.id
        preserved_access = session.get(RepositoryAccess, preserved_ids["access"])
        assert preserved_access is not None
        assert preserved_access.user_id == disabled.id
        audit_event = session.exec(
            select(AuditEvent).where(
                AuditEvent.action == "roles.configure_single_workspace"
            )
        ).one()
        assert audit_event.target_id == owner.id


def test_workspace_admin_only_lists_and_revokes_owned_tokens(
    client: TestClient, application
) -> None:
    actor = add_user(application, "actor@example.com", "workspace_admin")
    peer = add_user(application, "peer@example.com", "workspace_admin")
    with Session(application.state.engine) as session:
        own_token = ApiToken(
            name="own",
            token_prefix="cat_own",
            token_hash="a" * 64,
            created_by=actor.id,
        )
        peer_token = ApiToken(
            name="peer",
            token_prefix="cat_peer",
            token_hash="b" * 64,
            created_by=peer.id,
        )
        session.add_all([own_token, peer_token])
        session.commit()
        session.refresh(own_token)
        session.refresh(peer_token)
        own_token_id = own_token.id
        peer_token_id = peer_token.id

    csrf = login(client, actor.email)
    headers = {"X-CSRF-Token": csrf}
    listed = client.get("/api/v1/tokens")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [own_token_id]
    assert client.delete(
        f"/api/v1/tokens/{peer_token_id}", headers=headers
    ).status_code == 404
    assert client.delete(
        f"/api/v1/tokens/{own_token_id}", headers=headers
    ).status_code == 204

    with Session(application.state.engine) as session:
        stored_own = session.get(ApiToken, own_token_id)
        stored_peer = session.get(ApiToken, peer_token_id)
        assert stored_own is not None and stored_own.revoked_at is not None
        assert stored_peer is not None and stored_peer.revoked_at is None


def test_mcp_token_follows_the_owners_current_administrator_role(application) -> None:
    actor = add_user(application, "token-owner@example.com", "workspace_admin")
    raw_token = "cat_dynamic_role_probe"
    with Session(application.state.engine) as session:
        token = ApiToken(
            name="dynamic role",
            token_prefix=raw_token[:12],
            token_hash=digest_secret(raw_token),
            repository_ids_json='["repo-1"]',
            created_by=actor.id,
        )
        session.add(token)
        session.commit()
        token_id = token.id

    assert resolve_token_identity(application.state.engine, raw_token) is not None
    with Session(application.state.engine) as session:
        stored_actor = session.get(User, actor.id)
        assert stored_actor is not None
        stored_actor.role = "member"
        session.add(stored_actor)
        session.commit()

    assert resolve_token_identity(application.state.engine, raw_token) is None
    with Session(application.state.engine) as session:
        stored_token = session.get(ApiToken, token_id)
        assert stored_token is not None
        assert stored_token.revoked_at is None
        stored_actor = session.get(User, actor.id)
        assert stored_actor is not None
        stored_actor.role = "workspace_admin"
        session.add(stored_actor)
        session.commit()

    assert resolve_token_identity(application.state.engine, raw_token) is not None


def test_member_creation_waits_for_role_configuration_lock(
    client: TestClient, application, monkeypatch
) -> None:
    owner = add_user(application, "owner@example.com", "owner")
    csrf = login(client, owner.email)
    headers = {"X-CSRF-Token": csrf}
    monkeypatch.setattr(api, "hash_password", lambda _password: "test-hash")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with member_lifecycle_lock(application.state.engine):
            creating = executor.submit(
                lambda: client.post(
                    "/api/v1/members",
                    headers=headers,
                    json={
                        "email": "serialized@example.com",
                        "display_name": "Serialized",
                        "password": "another correct password",
                        "role": "member",
                    },
                )
            )
            try:
                creating.result(timeout=0.5)
                completed_before_lock_release = True
            except TimeoutError:
                completed_before_lock_release = False
        response = creating.result(timeout=10)

    assert completed_before_lock_release is False
    assert response.status_code == 201


def test_create_admin_cli_waits_for_role_configuration_lock(
    application, monkeypatch
) -> None:
    monkeypatch.setattr(cli, "resources", lambda: (object(), application.state.engine))
    monkeypatch.setattr(cli, "hash_password", lambda _password: "test-hash")
    args = SimpleNamespace(
        email="serialized-cli@example.com",
        name="Serialized CLI",
        password="another correct password",
        password_env="CODEATLAS_BOOTSTRAP_ADMIN_PASSWORD",
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with member_lifecycle_lock(application.state.engine):
            creating = executor.submit(cli.create_admin, args)
            try:
                creating.result(timeout=0.5)
                completed_before_lock_release = True
            except TimeoutError:
                completed_before_lock_release = False
        creating.result(timeout=10)

    assert completed_before_lock_release is False
    with Session(application.state.engine) as session:
        created = session.exec(
            select(User).where(User.email == "serialized-cli@example.com")
        ).one()
        assert created.role == "owner"


def test_configure_roles_cli_does_not_run_startup_recovery(monkeypatch, capsys) -> None:
    class FakeEngine:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    engine = FakeEngine()
    settings = object()
    monkeypatch.setattr(cli.Settings, "load", lambda: settings)
    monkeypatch.setattr(cli, "create_database", lambda loaded: engine)
    monkeypatch.setattr(
        cli,
        "initialize_database",
        lambda *_args: (_ for _ in ()).throw(AssertionError("startup recovery ran")),
    )
    monkeypatch.setattr(
        cli,
        "configure_single_workspace_roles",
        lambda used_engine, email: (
            {"owner": 1, "workspace_admin": 3}
            if used_engine is engine and email == "admin@example.com"
            else (_ for _ in ()).throw(AssertionError("wrong arguments"))
        ),
    )

    cli.configure_roles(SimpleNamespace(owner_email="admin@example.com"))

    assert engine.disposed is True
    assert capsys.readouterr().out.strip() == '{"owner": 1, "workspace_admin": 3}'


def test_role_bootstrap_fails_without_mutation_when_owner_is_missing(application) -> None:
    from codeatlas.roles import configure_single_workspace_roles

    user = add_user(application, "other@example.com", "admin")

    try:
        configure_single_workspace_roles(application.state.engine, "missing@example.com")
    except ValueError as exc:
        assert str(exc) == "Designated owner does not exist"
    else:
        raise AssertionError("missing owner should fail")

    with Session(application.state.engine) as session:
        stored = session.get(User, user.id)
        assert stored is not None
        assert stored.role == "admin"
