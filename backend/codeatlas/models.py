from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def new_id() -> str:
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)


class User(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    email: str = Field(index=True, unique=True, max_length=320)
    display_name: str = Field(max_length=100)
    password_hash: str = Field(sa_column=Column(Text, nullable=False))
    role: str = Field(default="member", index=True, max_length=20)
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utc_now)


class UserSession(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    user_id: str = Field(foreign_key="user.id", index=True, max_length=32)
    token_hash: str = Field(index=True, unique=True, max_length=64)
    csrf_token: str = Field(max_length=100)
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)


class GitLabSource(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    name: str = Field(index=True, unique=True, max_length=100)
    base_url: str = Field(max_length=500)
    group_path: str = Field(max_length=500)
    credential_ref: str = Field(max_length=200)
    enabled: bool = Field(default=True, index=True)
    poll_interval_seconds: int = Field(default=1800)
    last_checked_at: datetime | None = None
    last_error: str = Field(default="", max_length=2000)
    created_by: str = Field(foreign_key="user.id", max_length=32)
    created_at: datetime = Field(default_factory=utc_now)


class GitHubSource(SQLModel, table=True):
    """A GitHub repository polled for commits and indexed automatically."""

    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    name: str = Field(index=True, unique=True, max_length=100)
    repo_url: str = Field(max_length=500)
    owner: str = Field(max_length=100)
    repository: str = Field(max_length=100)
    branch: str = Field(default="main", max_length=200)
    credential_ref: str = Field(default="", max_length=200)
    repository_id: str = Field(
        foreign_key="repository.id", unique=True, index=True, max_length=32
    )
    ssh_key_path: str = Field(default="", max_length=1000)
    enabled: bool = Field(default=True, index=True)
    poll_interval_seconds: int = Field(default=1800)
    last_checked_at: datetime | None = None
    last_error: str = Field(default="", max_length=2000)
    created_by: str = Field(foreign_key="user.id", max_length=32)
    created_at: datetime = Field(default_factory=utc_now)


class DocumentCollection(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    name: str = Field(unique=True, max_length=120)
    description: str = Field(default="", max_length=500)
    created_by: str = Field(foreign_key="user.id", max_length=32)
    created_at: datetime = Field(default_factory=utc_now)


class Document(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    collection_id: str = Field(foreign_key="documentcollection.id", index=True, max_length=32)
    title: str = Field(max_length=300)
    original_filename: str = Field(max_length=500)
    mime_type: str = Field(max_length=120)
    status: str = Field(default="indexed", index=True, max_length=30)
    version: int = Field(default=1)
    source_path: str = Field(max_length=1000)
    sha256: str = Field(max_length=64)
    created_by: str = Field(foreign_key="user.id", max_length=32)
    created_at: datetime = Field(default_factory=utc_now)


class DocumentChunkRecord(SQLModel, table=True):
    __table_args__ = (
        Index(
            "ft_documentchunk_search",
            "title",
            "section",
            "content",
            mysql_prefix="FULLTEXT",
            mysql_with_parser="ngram",
        ),
    )
    id: str = Field(primary_key=True, max_length=64)
    document_id: str = Field(foreign_key="document.id", index=True, max_length=32)
    collection_id: str = Field(foreign_key="documentcollection.id", index=True, max_length=32)
    title: str = Field(max_length=300)
    section: str = Field(default="", max_length=500)
    page: int | None = None
    structure_type: str = Field(default="section", index=True, max_length=50)
    metadata_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))


class ExternalSource(SQLModel, table=True):
    """A configured external document system synchronized into a collection."""

    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    name: str = Field(index=True, unique=True, max_length=120)
    provider: str = Field(index=True, max_length=40)
    collection_id: str = Field(
        foreign_key="documentcollection.id", index=True, max_length=32
    )
    credential_ref: str = Field(max_length=200)
    config_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    enabled: bool = Field(default=True, index=True)
    poll_interval_seconds: int = Field(default=1800)
    sync_status: str = Field(default="idle", index=True, max_length=30)
    last_checked_at: datetime | None = None
    last_error: str = Field(default="", max_length=2000)
    last_result_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_by: str = Field(foreign_key="user.id", max_length=32)
    created_at: datetime = Field(default_factory=utc_now)


class ExternalSourceItem(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source_id", "external_id_hash"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    source_id: str = Field(foreign_key="externalsource.id", index=True, max_length=32)
    external_id: str = Field(sa_column=Column(Text, nullable=False))
    external_id_hash: str = Field(max_length=64)
    document_id: str | None = Field(
        default=None, foreign_key="document.id", index=True, max_length=32
    )
    path: str = Field(sa_column=Column(Text, nullable=False))
    title: str = Field(max_length=300)
    mime_type: str = Field(default="application/octet-stream", max_length=120)
    revision: str = Field(default="", max_length=500)
    modified_at: str = Field(default="", max_length=100)
    source_url: str = Field(default="", max_length=2000)
    last_synced_at: datetime | None = None
    deleted_at: datetime | None = None


class WikiPage(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    path: str = Field(index=True, max_length=500)
    title: str = Field(max_length=300)
    content: str = Field(sa_column=Column(Text, nullable=False))
    sources_json: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(default="published", index=True, max_length=30)
    created_by: str = Field(foreign_key="user.id", max_length=32)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EmbeddingProfile(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    name: str = Field(unique=True, max_length=120)
    base_url: str = Field(max_length=500)
    model: str = Field(max_length=200)
    dimension: int
    credential_ref: str = Field(max_length=200)
    backend: str = Field(default="chroma", max_length=30)
    provider: str = Field(default="openai", max_length=40)
    is_active: bool = Field(default=False, index=True)
    created_by: str = Field(foreign_key="user.id", max_length=32)
    created_at: datetime = Field(default_factory=utc_now)


class LlmProvider(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    name: str = Field(unique=True, max_length=120)
    base_url: str = Field(max_length=500)
    model: str = Field(max_length=200)
    api_key_ciphertext: str = Field(default="", sa_column=Column(Text, nullable=False))
    models_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    is_active: bool = Field(default=False, index=True)
    last_synced_at: datetime | None = None
    created_by: str = Field(foreign_key="user.id", max_length=32)
    created_at: datetime = Field(default_factory=utc_now)


class Repository(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    name: str = Field(index=True, unique=True, max_length=80)
    description: str = Field(default="", max_length=500)
    git_url: str = Field(max_length=1000)
    branch: str = Field(default="main", max_length=200)
    visibility: str = Field(default="private", index=True, max_length=20)
    license_name: str = Field(default="", max_length=100)
    license_url: str = Field(default="", max_length=1000)
    local_path: str = Field(default="", max_length=1000)
    status: str = Field(default="pending", index=True, max_length=30)
    active_generation_id: str | None = Field(default=None, index=True, max_length=32)
    chunk_count: int = Field(default=0)
    last_commit: str = Field(default="", max_length=64)
    last_indexed_at: datetime | None = None
    created_by: str = Field(foreign_key="user.id", max_length=32)
    source_id: str | None = Field(
        default=None, foreign_key="gitlabsource.id", index=True, max_length=32
    )
    external_project_id: str | None = Field(default=None, index=True, max_length=100)
    created_at: datetime = Field(default_factory=utc_now)


class RepositoryAccess(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("repository_id", "user_id", name="uq_repository_access"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    repository_id: str = Field(foreign_key="repository.id", index=True, max_length=32)
    user_id: str = Field(foreign_key="user.id", index=True, max_length=32)
    created_at: datetime = Field(default_factory=utc_now)


class ApiToken(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    name: str = Field(max_length=100)
    token_prefix: str = Field(index=True, max_length=16)
    token_hash: str = Field(unique=True, index=True, max_length=64)
    scopes_json: str = Field(
        default='["search","read","status"]',
        sa_column=Column(Text, nullable=False),
    )
    repository_ids_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    created_by: str = Field(foreign_key="user.id", max_length=32)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class IndexJob(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    repository_id: str = Field(foreign_key="repository.id", index=True, max_length=32)
    status: str = Field(default="queued", index=True, max_length=30)
    progress: int = Field(default=0)
    message: str = Field(default="", max_length=500)
    error: str = Field(default="", max_length=2000)
    commit: str = Field(default="", max_length=64)
    generation_id: str = Field(default="", max_length=64)
    created_by: str = Field(foreign_key="user.id", max_length=32)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class IndexGeneration(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    repository_id: str = Field(foreign_key="repository.id", index=True, max_length=32)
    commit: str = Field(max_length=64)
    status: str = Field(default="building", index=True, max_length=30)
    chunk_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=utc_now)
    activated_at: datetime | None = None


class CodeChunkRecord(SQLModel, table=True):
    __table_args__ = (
        Index(
            "ft_codechunkrecord_search",
            "path",
            "symbol",
            "content",
            mysql_prefix="FULLTEXT",
            mysql_with_parser="ngram",
        ),
    )

    id: str = Field(primary_key=True, max_length=64)
    generation_id: str = Field(foreign_key="indexgeneration.id", index=True, max_length=32)
    repository_id: str = Field(foreign_key="repository.id", index=True, max_length=32)
    commit: str = Field(max_length=64)
    path: str = Field(max_length=1000)
    language: str = Field(index=True, max_length=50)
    symbol: str = Field(max_length=500)
    start_line: int
    end_line: int
    content: str = Field(sa_column=Column(Text, nullable=False))


class AuditEvent(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True, max_length=32)
    actor_user_id: str | None = Field(default=None, index=True, max_length=32)
    action: str = Field(index=True, max_length=100)
    target_type: str = Field(max_length=50)
    target_id: str = Field(default="", max_length=100)
    detail_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, index=True)
