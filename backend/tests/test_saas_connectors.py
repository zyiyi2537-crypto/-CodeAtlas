from __future__ import annotations

import httpx

from codeatlas.connectors import ConfluenceConnector, NotionConnector


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
