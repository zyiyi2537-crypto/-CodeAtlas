from __future__ import annotations

import httpx
import pytest

from codeatlas.gitlab import GitLabClient, GitLabClientError


def test_list_group_projects_paginates_and_returns_project_metadata() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 101,
                        "path_with_namespace": "platform/orders",
                        "name": "orders",
                        "description": "Orders service",
                        "default_branch": "main",
                        "web_url": "https://gitlab.example.com/platform/orders",
                        "http_url_to_repo": "https://gitlab.example.com/platform/orders.git",
                    }
                ],
                headers={"X-Next-Page": "2"},
                request=request,
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": 102,
                    "path_with_namespace": "platform/payments",
                    "name": "payments",
                    "description": None,
                    "default_branch": "develop",
                    "web_url": "https://gitlab.example.com/platform/payments",
                    "http_url_to_repo": "https://gitlab.example.com/platform/payments.git",
                }
            ],
            headers={"X-Next-Page": ""},
            request=request,
        )

    client = GitLabClient(
        "https://gitlab.example.com",
        "test-token",
        transport=httpx.MockTransport(handler),
    )

    projects = client.list_group_projects("platform")

    assert [project.external_id for project in projects] == ["101", "102"]
    assert projects[0].git_url.endswith("orders.git")
    assert projects[1].description == ""
    assert [request.headers["PRIVATE-TOKEN"] for request in requests] == [
        "test-token",
        "test-token",
    ]
    assert [request.url.params["page"] for request in requests] == ["1", "2"]


def test_gitlab_client_redacts_token_from_errors() -> None:
    token = "secret-gitlab-token"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"invalid token {token}", request=request)

    client = GitLabClient(
        "https://gitlab.example.com",
        token,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(GitLabClientError, match="GitLab request failed") as error:
        client.list_group_projects("platform")

    assert token not in str(error.value)
    assert "[REDACTED]" in str(error.value)


def test_gitlab_client_rejects_untrusted_base_url() -> None:
    with pytest.raises(ValueError, match="HTTPS GitLab base URL"):
        GitLabClient("http://gitlab.example.com", "token")
