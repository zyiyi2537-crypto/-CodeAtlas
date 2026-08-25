from __future__ import annotations

import httpx
import pytest

from codeatlas.connectors import (
    ConfluenceConnector,
    NotionConnector,
    build_pinned_httpx_transport,
    resolve_public_endpoint,
)


def test_external_endpoint_resolution_rejects_dns_rebinding(monkeypatch) -> None:
    answers = iter([
        [(0, 0, 0, "", ("8.8.8.8", 443))],
        [(0, 0, 0, "", ("127.0.0.1", 443))],
    ])
    monkeypatch.setattr(
        "codeatlas.connectors.socket.getaddrinfo",
        lambda *_args, **_kwargs: next(answers),
    )

    pinned = resolve_public_endpoint("https://connector.example/wiki")
    assert pinned.addresses == ("8.8.8.8",)
    assert pinned.hostname == "connector.example"

    with pytest.raises(ValueError, match="non-public"):
        resolve_public_endpoint("https://connector.example/wiki")


@pytest.mark.parametrize(
    "address",
    [
        "64:ff9b::7f00:1",
        "64:ff9b:1::7f00:1",
        "2002:7f00:1::",
        "2001:0000:4136:e378:8000:63bf:3fff:fdd2",
        "224.0.0.1",
        "ff02::1",
    ],
)
def test_external_endpoint_rejects_ip_transition_addresses(
    monkeypatch, address: str
) -> None:
    monkeypatch.setattr(
        "codeatlas.connectors.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", (address, 443))],
    )

    with pytest.raises(ValueError, match="non-public"):
        resolve_public_endpoint("https://connector.example/wiki")


def test_allowlisted_confluence_accepts_only_ordinary_private_address(monkeypatch) -> None:
    monkeypatch.setenv("CODEATLAS_ALLOWED_EXTERNAL_HOSTS", "confluence.internal")
    monkeypatch.setattr(
        "codeatlas.connectors.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("10.20.30.40", 443))],
    )

    endpoint = resolve_public_endpoint(
        "https://confluence.internal/wiki", allow_private_host=True
    )

    assert endpoint.addresses == ("10.20.30.40",)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "::ffff:127.0.0.1",
        "64:ff9b::7f00:1",
        "2002:7f00:1::",
        "2001:0000:4136:e378:8000:63bf:3fff:fdd2",
        "224.0.0.1",
        "ff02::1",
    ],
)
def test_allowlisted_confluence_rejects_unsafe_private_or_transition_address(
    monkeypatch, address: str
) -> None:
    monkeypatch.setenv("CODEATLAS_ALLOWED_EXTERNAL_HOSTS", "confluence.internal")
    monkeypatch.setattr(
        "codeatlas.connectors.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", (address, 443))],
    )

    with pytest.raises(ValueError, match="non-public"):
        resolve_public_endpoint(
            "https://confluence.internal/wiki", allow_private_host=True
        )


def test_external_endpoint_rejects_port_zero(monkeypatch) -> None:
    monkeypatch.setattr(
        "codeatlas.connectors.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("8.8.8.8", 0))],
    )

    with pytest.raises(ValueError, match="invalid port"):
        resolve_public_endpoint("https://connector.example:0/wiki")


def test_httpx_transport_connects_to_the_validated_address(monkeypatch) -> None:
    endpoint = type(
        "Endpoint",
        (),
        {
            "hostname": "connector.example",
            "addresses": ("8.8.8.8",),
        },
    )()
    connected: list[tuple[str, int]] = []

    class FakeSocket:
        def settimeout(self, _timeout):
            return None

        def setsockopt(self, *_args):
            return None

        def getsockname(self):
            return ("127.0.0.1", 12345)

        def getpeername(self):
            return ("8.8.8.8", 443)

        def close(self):
            return None

    monkeypatch.setattr(
        "httpcore._backends.sync.socket.create_connection",
        lambda address, *_args, **_kwargs: (connected.append(address) or FakeSocket()),
    )
    transport = build_pinned_httpx_transport(endpoint)
    try:
        stream = transport._pool._network_backend.connect_tcp(
            "connector.example", 443, timeout=1
        )
        stream.close()
    finally:
        transport.close()

    assert connected == [("8.8.8.8", 443)]


def test_notion_connector_paginates_pages_and_renders_structured_markdown() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/v1/search":
            body = request.read().decode()
            if "cursor-2" not in body:
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "object": "page",
                                "id": "page-1",
                                "last_edited_time": "2026-08-25T00:00:00.000Z",
                                "url": "https://notion.so/page-1",
                                "properties": {
                                    "title": {
                                        "type": "title",
                                        "title": [{"plain_text": "Runbook"}],
                                    }
                                },
                            }
                        ],
                        "has_more": True,
                        "next_cursor": "cursor-2",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "object": "page",
                            "id": "page-2",
                            "last_edited_time": "2026-08-25T01:00:00.000Z",
                            "url": "https://notion.so/page-2",
                            "properties": {"Name": {"title": [{"plain_text": "Architecture"}]}},
                        }
                    ],
                    "has_more": False,
                },
            )
        if request.url.path == "/v1/blocks/page-1/children":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "h-1",
                            "type": "heading_1",
                            "has_children": False,
                            "heading_1": {"rich_text": [{"plain_text": "Recovery"}]},
                        },
                        {
                            "id": "p-1",
                            "type": "paragraph",
                            "has_children": True,
                            "paragraph": {"rich_text": [{"plain_text": "Restart safely."}]},
                        },
                    ],
                    "has_more": False,
                },
            )
        if request.url.path == "/v1/blocks/p-1/children":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "nested-1",
                            "type": "bulleted_list_item",
                            "has_children": False,
                            "bulleted_list_item": {
                                "rich_text": [{"plain_text": "Verify health."}]
                            },
                        }
                    ],
                    "has_more": False,
                },
            )
        raise AssertionError(request.url)

    connector = NotionConnector(
        {},
        {"token": "test"},
        transport=httpx.MockTransport(handler),
    )

    items = connector.list_items()
    assert [item.external_id for item in items] == ["page-1", "page-2"]
    assert items[0].revision == "2026-08-25T00:00:00.000Z"
    content = connector.fetch(items[0]).decode()
    assert "# Runbook" in content
    assert "# Recovery" in content
    assert "Restart safely." in content
    assert "- Verify health." in content
    assert requests.count(("POST", "/v1/search")) == 2


def test_confluence_connector_paginates_pages_and_converts_storage_html() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/rest/api/content"):
            start = request.url.params.get("start", "0")
            if start == "0":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": "1001",
                                "title": "Deployment",
                                "_links": {"webui": "/spaces/ENG/pages/1001"},
                                "version": {"number": 4, "when": "2026-08-25T00:00:00Z"},
                            }
                        ],
                        "start": 0,
                        "limit": 1,
                        "size": 1,
                        "_links": {"next": "/rest/api/content?start=1"},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "1002",
                            "title": "API Guide",
                            "_links": {"webui": "/spaces/ENG/pages/1002"},
                            "version": {"number": 2, "when": "2026-08-25T01:00:00Z"},
                        }
                    ],
                    "start": 1,
                    "limit": 1,
                    "size": 1,
                    "_links": {},
                },
            )
        if request.url.path.endswith("/rest/api/content/1001"):
            return httpx.Response(
                200,
                json={
                    "id": "1001",
                    "title": "Deployment",
                    "body": {
                        "storage": {
                            "value": "<h1>Production</h1><p>Reload Nginx safely.</p>"
                        }
                    },
                },
            )
        raise AssertionError(request.url)

    connector = ConfluenceConnector(
        {
            "base_url": "https://company.atlassian.net/wiki",
            "space_key": "ENG",
            "deployment": "cloud",
        },
        {"email": "admin@example.com", "api_token": "test"},
        transport=httpx.MockTransport(handler),
        skip_network_validation=True,
    )

    items = connector.list_items()
    assert [item.external_id for item in items] == ["1001", "1002"]
    assert items[0].revision == "4"
    content = connector.fetch(items[0]).decode()
    assert "# Deployment" in content
    assert "# Production" in content
    assert "Reload Nginx safely." in content
    assert requests.count("/wiki/rest/api/content") == 2


def test_notion_root_page_limits_discovery_to_its_page_tree() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/pages/root-page":
            return httpx.Response(
                200,
                json={
                    "object": "page",
                    "id": "root-page",
                    "last_edited_time": "2026-08-25T00:00:00.000Z",
                    "url": "https://notion.so/root-page",
                    "properties": {
                        "title": {"title": [{"plain_text": "Root"}]}
                    },
                },
            )
        if request.url.path == "/v1/blocks/root-page/children":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "child-page",
                            "type": "child_page",
                            "has_children": False,
                            "child_page": {"title": "Child"},
                        }
                    ],
                    "has_more": False,
                },
            )
        if request.url.path == "/v1/pages/child-page":
            return httpx.Response(
                200,
                json={
                    "object": "page",
                    "id": "child-page",
                    "last_edited_time": "2026-08-25T01:00:00.000Z",
                    "url": "https://notion.so/child-page",
                    "properties": {
                        "title": {"title": [{"plain_text": "Child"}]}
                    },
                },
            )
        if request.url.path == "/v1/blocks/child-page/children":
            return httpx.Response(200, json={"results": [], "has_more": False})
        raise AssertionError(request.url)

    connector = NotionConnector(
        {"root_page_id": "root-page"},
        {"token": "test"},
        transport=httpx.MockTransport(handler),
    )

    assert [item.external_id for item in connector.list_items()] == [
        "root-page",
        "child-page",
    ]


def test_confluence_root_page_filters_unrelated_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/wiki/rest/api/content"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "root",
                        "title": "Root",
                        "ancestors": [],
                        "version": {"number": 1},
                    },
                    {
                        "id": "child",
                        "title": "Child",
                        "ancestors": [{"id": "root"}],
                        "version": {"number": 1},
                    },
                    {
                        "id": "unrelated",
                        "title": "Unrelated",
                        "ancestors": [{"id": "other"}],
                        "version": {"number": 1},
                    },
                ],
                "size": 3,
                "_links": {},
            },
        )

    connector = ConfluenceConnector(
        {
            "base_url": "https://company.atlassian.net/wiki",
            "space_key": "ENG",
            "root_page_id": "root",
            "deployment": "cloud",
        },
        {"email": "admin@example.com", "api_token": "test"},
        transport=httpx.MockTransport(handler),
        skip_network_validation=True,
    )

    assert [item.external_id for item in connector.list_items()] == ["root", "child"]
