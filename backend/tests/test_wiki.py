from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import login_admin


def test_admin_can_create_source_tracked_wiki_page(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/wiki/pages",
        headers={"X-CSRF-Token": csrf},
        json={
            "path": "services/order-service.md",
            "title": "订单服务",
            "content": "订单服务负责创建订单。",
            "sources": ["document://refund-standard/v1#section=Idempotency"],
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["source_type"] == "wiki"
    assert payload["sources"]

    search = client.post(
        "/api/v1/wiki/search",
        headers={"X-CSRF-Token": csrf},
        json={"query": "创建订单"},
    )
    assert search.status_code == 200
    assert search.json()[0]["path"] == "services/order-service.md"


def test_wiki_page_requires_sources(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/wiki/pages",
        headers={"X-CSRF-Token": csrf},
        json={"path": "empty.md", "title": "无来源", "content": "猜测"},
    )
    assert response.status_code == 422
