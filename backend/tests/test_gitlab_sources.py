from __future__ import annotations

from codeatlas.models import GitLabSource


def test_gitlab_source_stores_credential_reference_not_secret() -> None:
    source = GitLabSource(
        name="company-gitlab",
        base_url="https://gitlab.example.com",
        group_path="platform",
        credential_ref="gitlab-platform-readonly",
        created_by="admin-id",
    )
    assert source.credential_ref == "gitlab-platform-readonly"
    assert "token" not in source.model_dump()


def test_gitlab_source_defaults_to_enabled() -> None:
    source = GitLabSource(
        name="company-gitlab",
        base_url="https://gitlab.example.com",
        group_path="platform",
        credential_ref="gitlab-platform-readonly",
        created_by="admin-id",
    )
    assert source.enabled is True
    assert source.poll_interval_seconds == 1800
