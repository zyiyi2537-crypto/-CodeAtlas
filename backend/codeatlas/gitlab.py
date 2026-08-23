from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class GitLabProject:
    external_id: str
    path_with_namespace: str
    name: str
    description: str
    default_branch: str
    web_url: str
    git_url: str


class GitLabClientError(RuntimeError):
    """Raised when a GitLab API request cannot be completed safely."""


class GitLabClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        if not normalized.startswith("https://") or "@" in normalized:
            raise ValueError("GitLab base URL must be an HTTPS GitLab base URL")
        if not token.strip():
            raise ValueError("GitLab token must not be empty")
        self.base_url = normalized
        self._token = token.strip()
        self._client = httpx.Client(
            base_url=f"{normalized}/api/v4",
            headers={"PRIVATE-TOKEN": self._token, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitLabClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def list_group_projects(self, group: str, per_page: int = 100) -> list[GitLabProject]:
        group_path = group.strip().strip("/")
        if not group_path:
            raise ValueError("GitLab group must not be empty")
        if not 1 <= per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")

        projects: list[GitLabProject] = []
        page = 1
        while True:
            response = self._request(
                "GET",
                f"/groups/{quote(group_path, safe='')}/projects",
                params={
                    "page": page,
                    "per_page": per_page,
                    "include_subgroups": "true",
                    "with_shared": "false",
                },
            )
            payload = response.json()
            if not isinstance(payload, list):
                raise GitLabClientError("GitLab returned an invalid project list")
            projects.extend(self._project(item) for item in payload)
            next_page = response.headers.get("X-Next-Page", "").strip()
            if not next_page:
                return projects
            try:
                page = int(next_page)
            except ValueError as exc:
                raise GitLabClientError("GitLab returned an invalid pagination cursor") from exc

    def project_branch_commit(self, project_id: str | int, branch: str) -> str:
        if not branch.strip():
            raise ValueError("GitLab branch must not be empty")
        response = self._request(
            "GET",
            "/projects/"
            f"{quote(str(project_id), safe='')}/repository/branches/"
            f"{quote(branch, safe='')}",
        )
        payload = response.json()
        commit = payload.get("commit", {}).get("id") if isinstance(payload, dict) else None
        if not isinstance(commit, str) or not commit:
            raise GitLabClientError("GitLab returned no branch commit")
        return commit

    def project(self, project_id: str | int) -> GitLabProject:
        response = self._request("GET", f"/projects/{quote(str(project_id), safe='')}")
        return self._project(response.json())

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.replace(self._token, "[REDACTED]")[:300]
            raise GitLabClientError(
                f"GitLab request failed with HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            detail = str(exc).replace(self._token, "[REDACTED]")
            raise GitLabClientError(f"GitLab request failed: {detail}") from exc

    @staticmethod
    def _project(payload: object) -> GitLabProject:
        if not isinstance(payload, dict):
            raise GitLabClientError("GitLab returned an invalid project")
        required = (
            "id",
            "path_with_namespace",
            "name",
            "default_branch",
            "web_url",
            "http_url_to_repo",
        )
        if any(key not in payload for key in required):
            raise GitLabClientError("GitLab project response is missing required fields")
        return GitLabProject(
            external_id=str(payload["id"]),
            path_with_namespace=str(payload["path_with_namespace"]),
            name=str(payload["name"]),
            description=str(payload.get("description") or ""),
            default_branch=str(payload["default_branch"] or "main"),
            web_url=str(payload["web_url"]),
            git_url=str(payload["http_url_to_repo"]),
        )
