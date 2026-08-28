from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from codeatlas.app import create_app
from codeatlas.llm_config import decrypt_api_key
from codeatlas.models import AuditEvent, LlmProvider
from tests.conftest import login_admin


def _create_provider(client: TestClient, csrf: str, name: str = "managed-llm") -> dict:
    response = client.post(
        "/api/v1/llm/providers",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": name,
            "base_url": "https://llm.example/v1",
            "api_key": "browser-managed-llm-key-one",
            "model": "chat-one",
            "models": [{"id": "chat-one", "name": "Chat One"}],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_llm_provider_encrypts_replaces_keeps_and_clears_browser_key(
    client: TestClient, application, admin
) -> None:
    csrf = login_admin(client)
    created = _create_provider(client, csrf)
    provider_id = created["id"]
    assert created["api_key_configured"] is True
    assert "api_key" not in created
    assert "browser-managed-llm-key-one" not in str(created)

    with Session(application.state.engine) as session:
        provider = session.get(LlmProvider, provider_id)
        assert provider is not None
        assert "browser-managed-llm-key-one" not in provider.api_key_ciphertext
        assert decrypt_api_key(application.state.settings.data_dir, provider) == (
            "browser-managed-llm-key-one"
        )

    kept = client.patch(
        f"/api/v1/llm/providers/{provider_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "managed-llm-updated",
            "base_url": "https://llm.example/v1",
            "model": "chat-two",
            "models": [{"id": "chat-two", "name": "Chat Two"}],
            "api_key": "",
            "clear_api_key": False,
        },
    )
    assert kept.status_code == 200
    with Session(application.state.engine) as session:
        provider = session.get(LlmProvider, provider_id)
        assert provider is not None
        assert decrypt_api_key(application.state.settings.data_dir, provider) == (
            "browser-managed-llm-key-one"
        )

    replaced = client.patch(
        f"/api/v1/llm/providers/{provider_id}",
        headers={"X-CSRF-Token": csrf},
        json={"api_key": "browser-managed-llm-key-two"},
    )
    assert replaced.status_code == 200
    assert "browser-managed-llm-key-two" not in replaced.text
    with Session(application.state.engine) as session:
        provider = session.get(LlmProvider, provider_id)
        assert provider is not None
        assert decrypt_api_key(application.state.settings.data_dir, provider) == (
            "browser-managed-llm-key-two"
        )

    cleared = client.patch(
        f"/api/v1/llm/providers/{provider_id}",
        headers={"X-CSRF-Token": csrf},
        json={"clear_api_key": True},
    )
    assert cleared.status_code == 200
    assert cleared.json()["api_key_configured"] is False
    with Session(application.state.engine) as session:
        provider = session.get(LlmProvider, provider_id)
        assert provider is not None
        assert provider.api_key_ciphertext == ""
        audit_payload = " ".join(
            event.detail_json
            for event in session.exec(select(AuditEvent)).all()
        )
        assert "browser-managed-llm-key-one" not in audit_payload
        assert "browser-managed-llm-key-two" not in audit_payload


def test_new_llm_provider_encrypts_the_trimmed_key(
    client: TestClient, application, admin
) -> None:
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/llm/providers",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "trimmed-key-provider",
            "base_url": "https://llm.example/v1",
            "api_key": "  browser-key-with-padding  ",
            "model": "chat-one",
        },
    )

    assert response.status_code == 201
    with Session(application.state.engine) as session:
        provider = session.get(LlmProvider, response.json()["id"])
        assert provider is not None
        assert decrypt_api_key(application.state.settings.data_dir, provider) == (
            "browser-key-with-padding"
        )


def test_llm_provider_tests_saved_key_without_returning_it(
    client: TestClient, application, admin, monkeypatch
) -> None:
    csrf = login_admin(client)
    created = _create_provider(client, csrf, "testable-llm")
    captured: dict[str, str] = {}

    def fake_sync(base_url: str, api_key: str):
        captured.update(base_url=base_url, api_key=api_key)
        return [{"id": "chat-one", "name": "Chat One"}]

    monkeypatch.setattr("codeatlas.api.sync_models", fake_sync)
    response = client.post(
        f"/api/v1/llm/providers/{created['id']}/test",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert response.json() == {"models": [{"id": "chat-one", "name": "Chat One"}], "count": 1}
    assert captured == {
        "base_url": "https://llm.example/v1",
        "api_key": "browser-managed-llm-key-one",
    }
    assert captured["api_key"] not in response.text


def test_saved_llm_key_is_not_sent_to_an_unsaved_base_url(
    client: TestClient, admin, monkeypatch
) -> None:
    csrf = login_admin(client)
    created = _create_provider(client, csrf, "host-bound-llm")
    called = False

    def fake_sync(_base_url: str, _api_key: str):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr("codeatlas.api.sync_models", fake_sync)
    response = client.post(
        "/api/v1/llm/providers/sync",
        headers={"X-CSRF-Token": csrf},
        json={
            "provider_id": created["id"],
            "base_url": "https://untrusted.example/v1",
            "api_key": "",
        },
    )

    assert response.status_code == 422
    assert called is False
    assert "replacement API key" in response.text

    saved = client.patch(
        f"/api/v1/llm/providers/{created['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"base_url": "https://untrusted.example/v1", "api_key": ""},
    )
    assert saved.status_code == 422
    providers = client.get("/api/v1/llm/providers").json()
    assert providers[0]["base_url"] == "https://llm.example/v1"


def test_active_llm_provider_cannot_clear_or_delete_only_key(
    client: TestClient, admin
) -> None:
    csrf = login_admin(client)
    created = _create_provider(client, csrf, "active-llm")
    provider_id = created["id"]
    activated = client.post(
        f"/api/v1/llm/providers/{provider_id}/activate",
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200

    cleared = client.patch(
        f"/api/v1/llm/providers/{provider_id}",
        headers={"X-CSRF-Token": csrf},
        json={"clear_api_key": True},
    )
    assert cleared.status_code == 409
    deleted = client.delete(
        f"/api/v1/llm/providers/{provider_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 409


def test_inactive_llm_provider_can_be_deleted(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    created = _create_provider(client, csrf, "delete-llm")
    response = client.delete(
        f"/api/v1/llm/providers/{created['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 204
    assert client.get("/api/v1/llm/providers").json() == []


def test_production_http_rejects_browser_managed_provider_key(
    settings, admin
) -> None:
    insecure = replace(
        settings,
        environment="production",
        public_origin="http://insecure.example",
        cookie_secure=False,
    )
    app = create_app(insecure)
    try:
        with TestClient(app) as insecure_client:
            with Session(app.state.engine) as session:
                stored_admin = session.get(type(admin), admin.id)
                if stored_admin is None:
                    session.add(admin)
                    session.commit()
            login = insecure_client.post(
                "/api/v1/auth/login",
                json={
                    "email": "admin@example.com",
                    "password": "correct horse battery staple",
                },
                headers={"Origin": "http://insecure.example"},
            )
            assert login.status_code == 200
            response = insecure_client.post(
                "/api/v1/llm/providers",
                headers={"X-CSRF-Token": login.json()["csrf_token"]},
                json={
                    "name": "blocked-http-provider",
                    "base_url": "https://llm.example/v1",
                    "api_key": "must-not-cross-http",
                    "model": "chat",
                },
            )
            assert response.status_code == 503
            assert "HTTPS" in response.text
            assert "must-not-cross-http" not in response.text
    finally:
        app.state.engine.dispose()


def test_production_https_configuration_still_rejects_an_actual_http_key_request(
    settings, admin
) -> None:
    production = replace(
        settings,
        environment="production",
        public_origin="https://secure.example",
        cookie_secure=True,
    )
    app = create_app(production)
    try:
        with TestClient(app, base_url="https://secure.example") as secure_client:
            with Session(app.state.engine) as session:
                if session.get(type(admin), admin.id) is None:
                    session.add(admin)
                    session.commit()
            login = secure_client.post(
                "/api/v1/auth/login",
                json={
                    "email": "admin@example.com",
                    "password": "correct horse battery staple",
                },
                headers={"Origin": "https://secure.example"},
            )
            assert login.status_code == 200
            csrf = login.json()["csrf_token"]
            session_cookie = login.cookies.get("codeatlas_session")
            assert session_cookie
            insecure = secure_client.post(
                "http://secure.example/api/v1/llm/providers",
                headers={
                    "X-CSRF-Token": csrf,
                    "Cookie": f"codeatlas_session={session_cookie}",
                },
                json={
                    "name": "actual-http-provider",
                    "base_url": "https://llm.example/v1",
                    "api_key": "actual-http-key",
                    "model": "chat",
                },
            )
            assert insecure.status_code == 503
            assert "actual-http-key" not in insecure.text

            accepted = secure_client.post(
                "/api/v1/llm/providers",
                headers={
                    "X-CSRF-Token": csrf,
                    "X-Forwarded-Proto": "https",
                },
                json={
                    "name": "actual-https-provider",
                    "base_url": "https://llm.example/v1",
                    "api_key": "actual-https-key",
                    "model": "chat",
                },
            )
            assert accepted.status_code == 201
            assert "actual-https-key" not in accepted.text
    finally:
        app.state.engine.dispose()


def test_validation_errors_never_echo_provider_api_key(
    client: TestClient, admin
) -> None:
    csrf = login_admin(client)
    oversized_key = "sensitive-validation-key-" + ("x" * 1100)

    response = client.post(
        "/api/v1/llm/providers",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "invalid-key-length",
            "base_url": "https://llm.example/v1",
            "model": "chat",
            "api_key": oversized_key,
        },
    )

    assert response.status_code == 422
    assert oversized_key not in response.text
    assert "sensitive-validation-key" not in response.text
