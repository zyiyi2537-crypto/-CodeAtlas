from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from .auth import (
    clear_browser_session,
    create_browser_session,
    require_admin,
    require_csrf,
    require_identity,
    resolve_identity,
)
from .authorization import AuthorizationScope, resolve_authorization_scope
from .chat import ChatService, ChatUnavailableError
from .chat_session_lock import (
    ChatSessionLockError,
    acquire_chat_session_lock,
    release_chat_session_lock,
)
from .connectors import credential_environment_name, validate_public_https_base_url
from .conventions import find_company_conventions, serialize_convention
from .credential_crypto import CredentialEncryptionError, encrypt_secret
from .documents import chunk_document, extract_structured_blocks
from .embedding_profile_lock import (
    EmbeddingProfileLockError,
    embedding_profile_lock,
)
from .embeddings import (
    EmbeddingClient,
    embedding_credential_name,
    resolve_embedding_api_key,
    settings_for_profile,
)
from .github import (
    GitHubBranchNotFoundError,
    GitHubError,
    generate_deploy_key,
    remote_commit,
    repository_identity,
    resolve_deploy_key,
)
from .gitlab import GitLabClient, GitLabClientError
from .index_job_schedule_lock import (
    IndexJobScheduleLockError,
    index_job_schedule_lock,
)
from .job_queue import ActiveIndexJobError, JobRequest
from .llm_config import (
    LlmProviderError,
    decrypt_api_key,
    encrypt_api_key,
    new_provider_name,
    normalize_base_url,
    sync_models,
)
from .llm_provider_lock import LlmProviderLockError, llm_provider_lock
from .member_lifecycle_lock import MemberLifecycleLockError, member_lifecycle_lock
from .models import (
    DEFAULT_SPACE_ID,
    ApiToken,
    AuditEvent,
    ChatMessage,
    ChatSession,
    CodeChunkRecord,
    CompanyConvention,
    Document,
    DocumentChunkRecord,
    DocumentCollection,
    EmbeddingProfile,
    ExternalSource,
    GitHubSource,
    GitLabSource,
    IndexJob,
    KnowledgeSpace,
    LlmProvider,
    Repository,
    RepositoryAccess,
    User,
    UserMemory,
    UserSession,
    WikiPage,
    new_id,
    utc_now,
)
from .roles import (
    ASSIGNABLE_ROLES,
    MEMBER_ROLE,
    OWNER_ROLE,
    can_assign_role,
    can_manage_role,
    is_admin_role,
    is_owner_role,
)
from .security import (
    contains_secret,
    digest_secret,
    hash_password,
    mask_credential_ref,
    new_secret,
    redact_secrets,
    validate_credential_ref,
    validate_git_branch,
    validate_git_url,
    validate_public_git_url,
    validate_repository_name,
    verify_password,
)
from .vector_store import (
    VectorStore,
    delete_profile_collections,
    profile_contains_generation,
)

login_attempts: dict[str, list[float]] = {}
login_ip_attempts: dict[str, list[float]] = {}
login_attempts_lock = threading.Lock()
LOGIN_LIMIT = 5
LOGIN_IP_LIMIT = 20
LOGIN_WINDOW = 300
MAX_LOGIN_IDENTIFIERS = 10_000
MAX_CONCURRENT_LOGIN_VERIFICATIONS = 2
active_login_verifications = 0
active_login_ips: set[str] = set()
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=2$Vsv1tXmyXuV69wZ5pQfAkQ$"
    "RYCMsSBrtS7PjMRGsY11r5XtYvRreCGelXMm++IFlbU"
)


def _prune_login_attempts(store: dict[str, list[float]], now: float) -> None:
    expired = [
        key
        for key, values in store.items()
        if not values or now - values[-1] >= LOGIN_WINDOW
    ]
    for key in expired:
        store.pop(key, None)


def _active_login_attempts(
    store: dict[str, list[float]], identifier: str, now: float
) -> list[float]:
    attempts = store.get(identifier, [])
    attempts = [timestamp for timestamp in attempts if now - timestamp < LOGIN_WINDOW]
    if attempts:
        store[identifier] = attempts
    else:
        store.pop(identifier, None)
    return attempts


def _record_login_attempt(
    store: dict[str, list[float]],
    identifier: str,
    attempts: list[float],
    now: float,
) -> None:
    if identifier not in store and len(store) >= MAX_LOGIN_IDENTIFIERS:
        oldest = min(store, key=lambda key: store[key][-1])
        store.pop(oldest, None)
    store[identifier] = [*attempts, now]


def _check_and_record_login_attempt(
    store: dict[str, list[float]], identifier: str, limit: int, now: float
) -> None:
    attempts = _active_login_attempts(store, identifier, now)
    if len(attempts) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts. Please try again later.",
        )
    _record_login_attempt(store, identifier, attempts, now)


def check_login_rate_limit(identifier: str) -> None:
    now = time.time()
    with login_attempts_lock:
        _prune_login_attempts(login_attempts, now)
        _check_and_record_login_attempt(login_attempts, identifier, LOGIN_LIMIT, now)


def check_login_rate_limits(account_identifier: str, ip_identifier: str) -> None:
    now = time.time()
    with login_attempts_lock:
        _prune_login_attempts(login_attempts, now)
        _prune_login_attempts(login_ip_attempts, now)
        account_attempts = _active_login_attempts(
            login_attempts, account_identifier, now
        )
        ip_attempts = _active_login_attempts(login_ip_attempts, ip_identifier, now)
        if len(account_attempts) >= LOGIN_LIMIT or len(ip_attempts) >= LOGIN_IP_LIMIT:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many login attempts. Please try again later.",
            )
        _record_login_attempt(login_attempts, account_identifier, account_attempts, now)
        _record_login_attempt(login_ip_attempts, ip_identifier, ip_attempts, now)


@contextmanager
def login_verification_slot(ip_identifier: str) -> Iterator[None]:
    global active_login_verifications
    with login_attempts_lock:
        if (
            active_login_verifications >= MAX_CONCURRENT_LOGIN_VERIFICATIONS
            or ip_identifier in active_login_ips
        ):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many concurrent login attempts. Please try again later.",
            )
        active_login_verifications += 1
        active_login_ips.add(ip_identifier)
    try:
        yield
    finally:
        with login_attempts_lock:
            active_login_verifications -= 1
            active_login_ips.discard(ip_identifier)


def clear_login_rate_limit(identifier: str) -> None:
    with login_attempts_lock:
        login_attempts.pop(identifier, None)

router = APIRouter(prefix="/api/v1")


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class RepositoryCreate(BaseModel):
    name: str
    description: str = Field(default="", max_length=500)
    git_url: str
    branch: str = Field(default="main", max_length=200)
    visibility: str = "private"
    license_name: str = Field(default="", max_length=100)
    license_url: str = Field(default="", max_length=1000)
    space_id: str = Field(default=DEFAULT_SPACE_ID, max_length=32)


class GitLabSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    group_path: str = Field(min_length=1, max_length=500)
    credential_ref: str = Field(min_length=1, max_length=200)
    poll_interval_seconds: int = Field(default=1800, ge=300, le=86400)


class GitLabProjectImport(BaseModel):
    external_project_id: str = Field(min_length=1, max_length=100)
    visibility: str = "private"


class GitHubSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    repo_url: str = Field(min_length=1, max_length=500)
    branch: str = Field(default="main", max_length=200)
    ssh_key_id: str = Field(default="", max_length=64)
    poll_interval_seconds: int = Field(default=1800, ge=300, le=86400)
    visibility: str = "private"
    description: str = Field(default="", max_length=500)


class DocumentCollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    space_id: str = Field(default=DEFAULT_SPACE_ID, max_length=32)


class ExternalSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=40)
    collection_id: str = Field(min_length=1, max_length=32)
    credential_ref: str = Field(min_length=1, max_length=200)
    config: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    poll_interval_seconds: int = Field(default=1800, ge=300, le=86400)


class DocumentSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    collection_ids: list[str] = Field(default_factory=list, max_length=20)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    source_types: list[str] = Field(
        default_factory=lambda: ["code", "document", "wiki"], max_length=3
    )
    collection_ids: list[str] = Field(default_factory=list, max_length=20)
    repository_ids: list[str] = Field(default_factory=list, max_length=20)


class WikiPageCreate(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    sources: list[str] = Field(min_length=1, max_length=50)
    space_id: str = Field(default=DEFAULT_SPACE_ID, max_length=32)


class WikiSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class ConventionCitation(BaseModel):
    repository_id: str = Field(min_length=1, max_length=32)
    commit: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=1000)
    symbol: str = Field(default="", max_length=500)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class CompanyConventionCreate(BaseModel):
    space_id: str = Field(default=DEFAULT_SPACE_ID, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    language: str = Field(default="", max_length=50)
    framework: str = Field(default="", max_length=100)
    task: str = Field(default="", max_length=200)
    rule: str = Field(min_length=1, max_length=5000)
    prohibited_pattern: str = Field(default="", max_length=5000)
    examples: list[str] = Field(default_factory=list, max_length=20)
    citations: list[ConventionCitation] = Field(min_length=1, max_length=20)
    status: str = "draft"


class CompanyConventionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=50)
    language: str | None = Field(default=None, max_length=50)
    framework: str | None = Field(default=None, max_length=100)
    task: str | None = Field(default=None, max_length=200)
    rule: str | None = Field(default=None, min_length=1, max_length=5000)
    prohibited_pattern: str | None = Field(default=None, max_length=5000)
    examples: list[str] | None = Field(default=None, max_length=20)
    citations: list[ConventionCitation] | None = Field(default=None, min_length=1, max_length=20)
    status: str | None = None


class EmbeddingProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    dimension: int = Field(ge=64, le=4096)
    credential_ref: str = Field(default="", max_length=200)
    backend: str = "chroma"
    provider: str = "openai"
    api_key: str = Field(default="", max_length=1000)


class EmbeddingProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    dimension: int | None = Field(default=None, ge=64, le=4096)
    credential_ref: str | None = Field(default=None, min_length=1, max_length=200)
    provider: str | None = None
    api_key: str = Field(default="", max_length=1000)
    clear_api_key: bool = False


class EmbeddingProfileProbe(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    credential_ref: str = Field(default="", max_length=200)
    provider: str = "openai"
    api_key: str = Field(default="", max_length=1000)
    profile_id: str | None = Field(default=None, max_length=32)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    repository_ids: list[str] = Field(default_factory=list, max_length=20)
    languages: list[str] = Field(default_factory=list, max_length=10)
    path_prefix: str = Field(default="", max_length=500)
    limit: int = Field(default=10, ge=1, le=10)


class ChatTurn(BaseModel):
    role: str
    content: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    repository_ids: list[str] = Field(default_factory=list, max_length=20)
    history: list[ChatTurn] = Field(default_factory=list, max_length=6)


class ChatSessionCreate(BaseModel):
    title: str = Field(default="新对话", max_length=200)
    repository_ids: list[str] = Field(default_factory=list, max_length=20)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)


class ChatMessageCreate(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)


class UserMemoryCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=30)
    content: str = Field(min_length=1, max_length=1000)


class LlmProviderCreate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str = Field(default="", max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    models: list[dict[str, str]] = Field(default_factory=list, max_length=500)


class LlmProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    models: list[dict[str, str]] | None = Field(default=None, max_length=500)
    api_key: str = Field(default="", max_length=1000)
    clear_api_key: bool = False


class LlmProviderSyncRequest(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str = Field(default="", max_length=1000)
    provider_id: str | None = Field(default=None, max_length=32)


class MemberCreate(BaseModel):
    email: str
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=200)
    role: str = "member"


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: ["search", "read", "status"])
    repository_ids: list[str] = Field(default_factory=list, max_length=50)
    space_ids: list[str] = Field(default_factory=list, max_length=20)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class MemberUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class SlidingWindowLimiter:
    def __init__(self):
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str, limit: int, window: int = 60) -> None:
        now = time.monotonic()
        with self.lock:
            events = self.events[key]
            while events and events[0] <= now - window:
                events.popleft()
            if len(events) >= limit:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")
            events.append(now)


limiter = SlidingWindowLimiter()


def require_browser_secret_transport(request: Request) -> None:
    settings = request.app.state.settings
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    request_scheme = (forwarded_proto.split(",", 1)[0].strip() or request.url.scheme).lower()
    if settings.environment == "production" and (
        not settings.public_origin.startswith("https://")
        or not settings.cookie_secure
        or request_scheme != "https"
    ):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Browser-managed credentials require an HTTPS public origin and secure cookies",
        )


def check_provider_config_rate_limit(user_id: str) -> None:
    limiter.check(f"provider-config:{user_id}", 30)


def require_embedding_profile_mutation_lock(request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
    try:
        with embedding_profile_lock(request.app.state.engine):
            yield
    except EmbeddingProfileLockError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


def require_llm_provider_mutation_lock(request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
    try:
        with llm_provider_lock(request.app.state.engine):
            yield
    except LlmProviderLockError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


def require_index_job_schedule_lock(request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
    try:
        with index_job_schedule_lock(request.app.state.engine):
            yield
    except IndexJobScheduleLockError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


def require_embedding_activation_locks(request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
    try:
        with embedding_profile_lock(request.app.state.engine):
            with index_job_schedule_lock(request.app.state.engine):
                yield
    except (EmbeddingProfileLockError, IndexJobScheduleLockError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


def database(request: Request) -> Session:
    return Session(request.app.state.engine)


def authorization_scope(
    request: Request,
    session: Session,
    user: User | None,
    *,
    allow_anonymous: bool = False,
) -> AuthorizationScope:
    return resolve_authorization_scope(
        session,
        user,
        allow_anonymous_repositories=(
            allow_anonymous and request.app.state.settings.allow_anonymous_search
        ),
    )


def require_space(
    session: Session,
    scope: AuthorizationScope,
    space_id: str,
    action: str = "read",
) -> KnowledgeSpace:
    space = session.get(KnowledgeSpace, space_id)
    if space is None or not scope.permits_space(space.id, action):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Knowledge space is not accessible")
    return space


def validate_convention_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"draft", "inferred", "confirmed", "deprecated"}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Invalid company convention status",
        )
    return normalized


def validate_convention_citations(
    request: Request,
    session: Session,
    scope: AuthorizationScope,
    space_id: str,
    citations: list[ConventionCitation],
) -> list[dict]:
    normalized: list[dict] = []
    for citation in citations:
        if citation.end_line < citation.start_line:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Citation end_line must not precede start_line",
            )
        repository = session.get(Repository, citation.repository_id)
        if (
            repository is None
            or repository.space_id != space_id
            or repository.id not in scope.repository_ids
            or not repository.last_commit
            or repository.last_commit != citation.commit
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Convention citation must reference an accessible current repository commit",
            )
        try:
            preview = request.app.state.retriever.get_file(
                repository.id,
                citation.path,
                None,
                citation.start_line,
                citation.end_line,
                authorization_scope=scope,
            )
            if (
                not preview.get("content")
                or preview.get("start_line") != citation.start_line
                or preview.get("end_line") != citation.end_line
            ):
                raise ValueError("Citation line range is incomplete")
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Convention citation does not resolve to an accessible source range",
            ) from exc
        normalized.append(citation.model_dump())
    return normalized


def serialize_repository(repo: Repository) -> dict:
    return {
        "id": repo.id, "name": repo.name, "description": repo.description,
        "space_id": repo.space_id,
        "git_url": repo.git_url, "branch": repo.branch, "visibility": repo.visibility,
        "license_name": repo.license_name, "license_url": repo.license_url,
        "status": repo.status, "chunk_count": repo.chunk_count,
        "last_commit": repo.last_commit, "last_indexed_at": repo.last_indexed_at,
    }


def serialize_gitlab_source(source: GitLabSource) -> dict:
    return {
        "id": source.id,
        "name": source.name,
        "base_url": source.base_url,
        "group_path": source.group_path,
        "credential_ref": source.credential_ref,
        "enabled": source.enabled,
        "poll_interval_seconds": source.poll_interval_seconds,
        "last_checked_at": source.last_checked_at,
        "last_error": source.last_error,
        "created_at": source.created_at,
    }


def serialize_github_source(source: GitHubSource, repository: Repository | None = None) -> dict:
    return {
        "id": source.id,
        "name": source.name,
        "repo_url": source.repo_url,
        "owner": source.owner,
        "repository": source.repository,
        "branch": source.branch,
        "repository_id": source.repository_id,
        "repository_status": repository.status if repository else "unknown",
        "enabled": source.enabled,
        "poll_interval_seconds": source.poll_interval_seconds,
        "last_checked_at": source.last_checked_at,
        "last_error": source.last_error,
        "created_at": source.created_at,
        "deploy_key_configured": bool(source.ssh_key_path),
    }


def serialize_external_source(source: ExternalSource) -> dict:
    credential_env = credential_environment_name(source.credential_ref)
    try:
        config = json.loads(source.config_json or "{}")
        result = json.loads(source.last_result_json or "{}")
    except json.JSONDecodeError:
        config, result = {}, {}
    return {
        "id": source.id,
        "name": source.name,
        "provider": source.provider,
        "collection_id": source.collection_id,
        "credential_ref": mask_credential_ref(source.credential_ref),
        "credential_env": credential_env,
        "credential_configured": bool(os.getenv(credential_env, "")),
        "config": config,
        "enabled": source.enabled,
        "poll_interval_seconds": source.poll_interval_seconds,
        "sync_status": source.sync_status,
        "last_checked_at": source.last_checked_at,
        "last_error": source.last_error,
        "last_result": result,
        "created_at": source.created_at,
    }


def validate_external_source_config(provider: str, config: dict[str, str]) -> dict[str, str]:
    required = {
        "aws_s3": {"bucket", "region"},
        "tencent_cos": {"bucket", "region"},
        "notion": set(),
        "confluence": {"base_url", "space_key", "deployment"},
    }
    allowed = {
        "aws_s3": {"bucket", "prefix", "region", "endpoint_url"},
        "tencent_cos": {"bucket", "prefix", "region"},
        "notion": {"root_page_id"},
        "confluence": {"base_url", "space_key", "root_page_id", "deployment"},
    }
    if provider not in required:
        raise ValueError("provider must be aws_s3, tencent_cos, notion or confluence")
    normalized = {str(key): str(value).strip() for key, value in config.items()}
    if any(len(value) > 2000 for value in normalized.values()):
        raise ValueError("external source config values must not exceed 2000 characters")
    unknown = set(normalized) - allowed[provider]
    if unknown:
        raise ValueError(f"unsupported config fields: {', '.join(sorted(unknown))}")
    missing = [key for key in required[provider] if not normalized.get(key)]
    if missing:
        raise ValueError(f"missing required config fields: {', '.join(sorted(missing))}")
    if provider == "confluence" and normalized.get("deployment") not in {"cloud", "data_center"}:
        raise ValueError("Confluence deployment must be cloud or data_center")
    if provider == "confluence":
        normalized["base_url"] = validate_public_https_base_url(
            normalized["base_url"], allow_private_host=True
        )
    if provider == "aws_s3" and normalized.get("endpoint_url"):
        normalized["endpoint_url"] = validate_public_https_base_url(
            normalized["endpoint_url"]
        )
    return normalized


def gitlab_credential(request: Request, credential_ref: str) -> str:
    environment_name = f"CODEATLAS_CREDENTIAL_{credential_ref.upper().replace('-', '_')}"
    value = os.getenv(environment_name, "")
    if not value:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"GitLab credential reference is not configured: {credential_ref}",
        )
    return value


def validate_source_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    if url.strip().startswith("git@"):
        return validate_git_url(url, allowed_hosts)
    return validate_public_git_url(url, allowed_hosts)


def audit(
    session: Session,
    action: str,
    target_type: str,
    target_id: str,
    actor: str | None,
) -> None:
    session.add(AuditEvent(
        actor_user_id=actor, action=action, target_type=target_type, target_id=target_id
    ))


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response):
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != request.app.state.settings.public_origin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origin is not allowed")
    client_ip = request.client.host if request.client else "unknown"
    account_identifier = digest_secret(f"account:{payload.email.strip().lower()}")
    ip_identifier = digest_secret(f"ip:{client_ip}")
    check_login_rate_limits(account_identifier, ip_identifier)
    with database(request) as session:
        user = session.exec(
            select(User)
            .where(User.email == payload.email.strip().lower())
            .with_for_update()
        ).first()
        with login_verification_slot(ip_identifier):
            password_valid = verify_password(
                payload.password,
                user.password_hash if user else DUMMY_PASSWORD_HASH,
            )
        if not user or not user.is_active or not password_valid:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
        identity = create_browser_session(session, user, response, request.app.state.settings)
        clear_login_rate_limit(account_identifier)
        audit(session, "auth.login", "user", user.id, user.id)
        csrf_token = identity.session.csrf_token
        user_payload = public_user(user)
        session.commit()
        return {"user": user_payload, "csrf_token": csrf_token}


@router.get("/auth/me")
def me(request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        return {"user": public_user(identity.user), "csrf_token": identity.session.csrf_token}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response):
    with database(request) as session:
        identity = require_identity(request, session)
        require_csrf(request, identity)
        session.delete(identity.session)
        audit(session, "auth.logout", "user", identity.user.id, identity.user.id)
        session.commit()
    clear_browser_session(response)


@router.post("/auth/logout-all", status_code=204)
def logout_all(request: Request, response: Response):
    with database(request) as session:
        identity = require_identity(request, session)
        require_csrf(request, identity)
        sessions = session.exec(
            select(UserSession).where(UserSession.user_id == identity.user.id)
        ).all()
        for s in sessions:
            session.delete(s)
        audit(session, "auth.logout_all", "user", identity.user.id, identity.user.id)
        session.commit()
    clear_browser_session(response)


@router.get("/gitlab-sources")
def list_gitlab_sources(request: Request):
    with database(request) as session:
        require_admin(request, session)
        sources = session.exec(
            select(GitLabSource).order_by(col(GitLabSource.created_at).desc())
        ).all()
        return [serialize_gitlab_source(source) for source in sources]


@router.post("/github-keys", status_code=201)
def create_github_deploy_key(request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        key_id, public_key, _private_path = generate_deploy_key(request.app.state.settings)
        audit(session, "github_key.create", "github_key", key_id, identity.user.id)
        session.commit()
        return {"key_id": key_id, "public_key": public_key}


@router.get("/github-sources")
def list_github_sources(request: Request):
    with database(request) as session:
        require_admin(request, session)
        sources = session.exec(
            select(GitHubSource).order_by(col(GitHubSource.created_at).desc())
        ).all()
        return [
            serialize_github_source(source, session.get(Repository, source.repository_id))
            for source in sources
        ]


@router.post("/github-sources", status_code=201)
def create_github_source(payload: GitHubSourceCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        try:
            if payload.visibility not in {"public", "private"}:
                raise ValueError("Invalid visibility")
            is_ssh = payload.repo_url.strip().startswith("git@")
            if payload.visibility == "public" and is_ssh:
                raise ValueError("Public GitHub sources require an HTTPS clone URL")
            if payload.visibility == "private" and not is_ssh:
                raise ValueError("Private GitHub sources require an SSH clone URL")
            git_url = validate_source_url(
                payload.repo_url, request.app.state.settings.allowed_git_hosts
            )
            owner, repository_name = repository_identity(git_url)
            branch = validate_git_branch(payload.branch)
            if payload.visibility == "private":
                if not payload.ssh_key_id:
                    raise ValueError("Private GitHub sources require a Deploy Key")
                key_path = resolve_deploy_key(request.app.state.settings, payload.ssh_key_id)
            else:
                key_path = None
            remote_commit(
                request.app.state.settings,
                git_url,
                branch,
                key_path or "",
            )
            name = validate_repository_name(repository_name)
        except GitHubBranchNotFoundError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except GitHubError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if session.exec(
            select(GitHubSource).where(GitHubSource.name == payload.name.strip())
        ).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "GitHub source name already exists")
        if session.exec(select(Repository).where(Repository.name == name)).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Repository name already exists")
        repo = Repository(
            name=name,
            description=payload.description.strip(),
            git_url=git_url,
            branch=branch,
            visibility=payload.visibility,
            created_by=identity.user.id,
        )
        session.add(repo)
        session.flush()
        source = GitHubSource(
            name=payload.name.strip(),
            repo_url=git_url,
            owner=owner,
            repository=repository_name,
            branch=branch,
            repository_id=repo.id,
            ssh_key_path=str(key_path) if key_path else "",
            poll_interval_seconds=payload.poll_interval_seconds,
            created_by=identity.user.id,
        )
        session.add(source)
        audit(session, "github_source.create", "github_source", source.id, identity.user.id)
        session.commit()
        session.refresh(source)
        return serialize_github_source(source, repo)


@router.post("/github-sources/{source_id}/check")
def check_github_source(source_id: str, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        if not session.get(GitHubSource, source_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "GitHub source not found")
    queued = request.app.state.github_sync.check_source(source_id)
    return {"queued": queued}


@router.post("/document-collections", status_code=201)
def create_document_collection(payload: DocumentCollectionCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        scope = authorization_scope(request, session, identity.user)
        require_space(session, scope, payload.space_id, "manage")
        collection = DocumentCollection(
            name=payload.name.strip(),
            description=payload.description,
            space_id=payload.space_id,
            created_by=identity.user.id,
        )
        session.add(collection)
        session.commit()
        session.refresh(collection)
        return {
            "id": collection.id,
            "name": collection.name,
            "description": collection.description,
            "space_id": collection.space_id,
        }


@router.get("/document-collections")
def list_document_collections(request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        scope = authorization_scope(request, session, identity.user)
        if not scope.collection_ids:
            return []
        statement = (
            select(DocumentCollection)
            .where(col(DocumentCollection.id).in_(scope.collection_ids))
            .order_by(col(DocumentCollection.created_at).desc())
        )
        return [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "space_id": item.space_id,
            }
            for item in session.exec(statement).all()
        ]


@router.get("/external-sources")
def list_external_sources(request: Request):
    with database(request) as session:
        require_admin(request, session)
        sources = session.exec(
            select(ExternalSource).order_by(col(ExternalSource.created_at).desc())
        ).all()
        return [serialize_external_source(source) for source in sources]


@router.post("/external-sources", status_code=201)
def create_external_source(payload: ExternalSourceCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        if not session.get(DocumentCollection, payload.collection_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document collection not found")
        try:
            credential_ref = validate_credential_ref(payload.credential_ref)
            config = validate_external_source_config(payload.provider, payload.config)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        source = ExternalSource(
            name=payload.name.strip(),
            provider=payload.provider,
            collection_id=payload.collection_id,
            credential_ref=credential_ref,
            config_json=json.dumps(config, ensure_ascii=False),
            enabled=payload.enabled,
            poll_interval_seconds=payload.poll_interval_seconds,
            created_by=identity.user.id,
        )
        session.add(source)
        session.flush()
        audit(
            session,
            "external_source.create",
            "external_source",
            source.id,
            identity.user.id,
        )
        session.commit()
        session.refresh(source)
        return serialize_external_source(source)


@router.post("/external-sources/{source_id}/test")
def test_external_source(source_id: str, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        if not session.get(ExternalSource, source_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "External source not found")
    try:
        request.app.state.external_sync.test_source(source_id)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, redact_secrets(str(exc))
        ) from exc
    return {"status": "ok"}


@router.post("/external-sources/{source_id}/sync", status_code=202)
def sync_external_source(source_id: str, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        if not session.get(ExternalSource, source_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "External source not found")
    try:
        request.app.state.external_sync.submit(source_id)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"status": "queued"}


@router.delete("/external-sources/{source_id}", status_code=204)
def delete_external_source(source_id: str, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        if not session.get(ExternalSource, source_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "External source not found")
    try:
        request.app.state.external_sync.delete_source(source_id)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/document-collections/{collection_id}/documents")
def list_documents(collection_id: str, request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        scope = authorization_scope(request, session, identity.user)
        if not scope.permits_collection(collection_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Collection is not accessible")
        statement = select(Document).where(Document.collection_id == collection_id).order_by(
            col(Document.created_at).desc()
        )
        documents = session.exec(statement).all()
        result = []
        for item in documents:
            chunks = session.exec(
                select(DocumentChunkRecord).where(DocumentChunkRecord.document_id == item.id)
            ).all()
            result.append(
                {
                    "id": item.id,
                    "title": item.title,
                    "status": item.status,
                    "version": item.version,
                    "chunk_count": len(chunks),
                }
            )
        return result


@router.post("/document-collections/{collection_id}/documents", status_code=201)
async def upload_document(collection_id: str, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        scope = authorization_scope(request, session, identity.user)
        collection = session.get(DocumentCollection, collection_id)
        if not collection or not scope.permits_collection(collection_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document collection not found")
        require_space(session, scope, collection.space_id, "manage")
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read") or not hasattr(upload, "filename"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is required")
        filename = upload.filename or "document"
        content = await upload.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "document is too large")
        try:
            blocks = extract_structured_blocks(filename, content)
        except ValueError as exc:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
        title = str(form.get("title") or Path(filename).stem)
        content_type = getattr(upload, "content_type", None)
        document = Document(
            collection_id=collection.id,
            title=title[:300],
            original_filename=filename[:500],
            mime_type=content_type or "application/octet-stream",
            status=(
                "ocr_required"
                if blocks and all(block.kind == "ocr-required" for block in blocks)
                else "indexed"
            ),
            source_path="",
            sha256=__import__("hashlib").sha256(content).hexdigest(),
            created_by=identity.user.id,
        )
        document_path = request.app.state.settings.data_dir / "documents" / document.id
        document_path.mkdir(parents=True, exist_ok=True)
        raw_path = document_path / filename
        raw_path.write_bytes(content)
        document.source_path = str(raw_path)
        chunks = chunk_document(
            document.title,
            document.id,
            collection.id,
            blocks=blocks,
            space_id=collection.space_id,
        )
        session.add(document)
        session.add_all(chunks)
        session.commit()
        if chunks:
            try:
                request.app.state.knowledge_search.index_document(chunks)
            except Exception as exc:
                document.status = "index_failed"
                session.add(document)
                session.commit()
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    f"document embedding failed: {exc}",
                ) from exc
        return {
            "id": document.id,
            "title": document.title,
            "status": document.status,
            "version": document.version,
            "chunk_count": len(chunks),
        }


@router.post("/documents/search")
def search_documents(payload: DocumentSearchRequest, request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        scope = authorization_scope(request, session, identity.user)
    return request.app.state.knowledge_search.search_documents(
        payload.query, payload.collection_ids, scope
    )


@router.post("/knowledge/search")
def search_knowledge(payload: KnowledgeSearchRequest, request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        scope = authorization_scope(request, session, identity.user)
    allowed = {"code", "document", "wiki"}
    if not payload.source_types or not set(payload.source_types).issubset(allowed):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "source_types must contain code, document and/or wiki",
        )
    return request.app.state.retriever.search_knowledge(
        payload.query,
        identity.user,
        repository_ids=payload.repository_ids,
        collection_ids=payload.collection_ids,
        source_types=payload.source_types,
        authorization_scope=scope,
    )


@router.post("/wiki/pages", status_code=201)
def create_wiki_page(payload: WikiPageCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        scope = authorization_scope(request, session, identity.user)
        require_space(session, scope, payload.space_id, "manage")
        page = WikiPage(
            space_id=payload.space_id,
            path=payload.path.strip().lstrip("/"),
            title=payload.title.strip(),
            content=payload.content,
            sources_json=json.dumps(payload.sources, ensure_ascii=False),
            created_by=identity.user.id,
            updated_at=utc_now(),
        )
        if not page.path.endswith(".md"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Wiki path must end with .md"
            )
        if session.exec(
            select(WikiPage).where(
                WikiPage.space_id == page.space_id,
                WikiPage.path == page.path,
            )
        ).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Wiki page already exists")
        session.add(page)
        session.commit()
        session.refresh(page)
        try:
            request.app.state.knowledge_search.index_wiki(page)
        except Exception as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"wiki embedding failed: {exc}",
            ) from exc
        return {
            "id": page.id,
            "space_id": page.space_id,
            "path": page.path,
            "title": page.title,
            "content": page.content,
            "sources": payload.sources,
            "source_type": "wiki",
        }


@router.post("/wiki/search")
def search_wiki(payload: WikiSearchRequest, request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        scope = authorization_scope(request, session, identity.user)
    return request.app.state.knowledge_search.search_wiki(payload.query, scope)


@router.get("/spaces")
def list_spaces(request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        scope = authorization_scope(request, session, identity.user)
        if not scope.space_ids:
            return []
        spaces = session.exec(
            select(KnowledgeSpace)
            .where(col(KnowledgeSpace.id).in_(scope.space_ids))
            .order_by(col(KnowledgeSpace.created_at))
        ).all()
        roles = dict(scope.space_roles)
        return [
            {
                "id": space.id,
                "workspace_id": space.workspace_id,
                "name": space.name,
                "description": space.description,
                "visibility": space.visibility,
                "role": roles.get(space.id, "viewer"),
            }
            for space in spaces
        ]


@router.get("/company-conventions")
def list_company_conventions(
    request: Request,
    language: str = Query(default="", max_length=50),
    framework: str = Query(default="", max_length=100),
    task: str = Query(default="", max_length=200),
    space_id: str | None = Query(default=None, max_length=32),
):
    with database(request) as session:
        identity = require_identity(request, session)
        scope = authorization_scope(request, session, identity.user)
        if space_id:
            require_space(session, scope, space_id)
            scope = AuthorizationScope(
                actor_user_id=scope.actor_user_id,
                space_ids=(space_id,),
                repository_ids=scope.repository_ids,
                collection_ids=scope.collection_ids,
                actions=scope.actions,
                space_roles=scope.space_roles,
                repository_spaces=scope.repository_spaces,
                collection_spaces=scope.collection_spaces,
            )
        return find_company_conventions(
            session,
            scope,
            language=language,
            framework=framework,
            task=task,
            include_unconfirmed=is_admin_role(identity.user.role),
        )


@router.post("/company-conventions", status_code=201)
def create_company_convention(
    payload: CompanyConventionCreate, request: Request
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        scope = authorization_scope(request, session, identity.user)
        require_space(session, scope, payload.space_id, "manage")
        convention = CompanyConvention(
            space_id=payload.space_id,
            title=payload.title.strip(),
            category=payload.category.strip().lower(),
            language=payload.language.strip().lower(),
            framework=payload.framework.strip().lower(),
            task=payload.task.strip(),
            rule=payload.rule.strip(),
            prohibited_pattern=payload.prohibited_pattern.strip(),
            examples_json=json.dumps(payload.examples, ensure_ascii=False),
            citations_json=json.dumps(
                validate_convention_citations(
                    request, session, scope, payload.space_id, payload.citations
                ),
                ensure_ascii=False,
            ),
            status=validate_convention_status(payload.status),
            created_by=identity.user.id,
        )
        session.add(convention)
        audit(
            session,
            "company_convention.create",
            "company_convention",
            convention.id,
            identity.user.id,
        )
        session.commit()
        session.refresh(convention)
        return serialize_convention(convention)


@router.patch("/company-conventions/{convention_id}")
def update_company_convention(
    convention_id: str,
    payload: CompanyConventionUpdate,
    request: Request,
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        scope = authorization_scope(request, session, identity.user)
        convention = session.get(CompanyConvention, convention_id)
        if convention is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Company convention not found")
        require_space(session, scope, convention.space_id, "manage")
        changes = payload.model_dump(exclude_unset=True)
        if "status" in changes:
            convention.status = validate_convention_status(str(changes.pop("status")))
        if "citations" in changes:
            citations = [
                item
                if isinstance(item, ConventionCitation)
                else ConventionCitation.model_validate(item)
                for item in changes.pop("citations")
            ]
            convention.citations_json = json.dumps(
                validate_convention_citations(
                    request, session, scope, convention.space_id, citations
                ),
                ensure_ascii=False,
            )
        if "examples" in changes:
            convention.examples_json = json.dumps(
                changes.pop("examples"), ensure_ascii=False
            )
        for field_name in (
            "title",
            "category",
            "language",
            "framework",
            "task",
            "rule",
            "prohibited_pattern",
        ):
            if field_name in changes:
                value = str(changes[field_name]).strip()
                if field_name in {"category", "language", "framework"}:
                    value = value.lower()
                setattr(convention, field_name, value)
        convention.updated_at = utc_now()
        session.add(convention)
        audit(
            session,
            "company_convention.update",
            "company_convention",
            convention.id,
            identity.user.id,
        )
        session.commit()
        session.refresh(convention)
        return serialize_convention(convention)


@router.get("/embedding-profiles")
def list_embedding_profiles(request: Request):
    with database(request) as session:
        require_admin(request, session)
        return [
            _serialize_embedding_profile(item, request.app.state.settings.data_dir)
            for item in session.exec(select(EmbeddingProfile)).all()
        ]


def _serialize_embedding_profile(profile: EmbeddingProfile, data_dir: Path) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "base_url": profile.base_url,
        "model": profile.model,
        "dimension": profile.dimension,
        "credential_ref": mask_credential_ref(profile.credential_ref),
        "credential_configured": bool(resolve_embedding_api_key(profile, data_dir)),
        "credential_source": (
            "encrypted" if profile.api_key_ciphertext else
            "server_ref" if resolve_embedding_api_key(profile.credential_ref) else
            "none"
        ),
        "credential_env": embedding_credential_name(profile.credential_ref),
        "backend": profile.backend,
        "provider": profile.provider,
        "is_active": profile.is_active,
    }


@router.post("/embedding-profiles", status_code=201)
def create_embedding_profile(
    payload: EmbeddingProfileCreate,
    request: Request,
    _profile_lock: None = Depends(require_embedding_profile_mutation_lock),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        check_provider_config_rate_limit(identity.user.id)
        if payload.api_key.strip():
            require_browser_secret_transport(request)
        if payload.backend != "chroma":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only Chroma is implemented")
        if payload.provider not in {"openai", "tencent_multimodal"}:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "provider must be openai or tencent_multimodal",
            )
        profile_id = new_id()
        try:
            credential_ref = (
                validate_credential_ref(payload.credential_ref)
                if payload.credential_ref.strip()
                else f"embedding-{profile_id}"
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if session.exec(
            select(EmbeddingProfile).where(
                EmbeddingProfile.name == payload.name.strip()
            )
        ).first():
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Embedding profile name already exists"
            )
        try:
            ciphertext = encrypt_secret(
                request.app.state.settings.data_dir, payload.api_key.strip()
            )
        except CredentialEncryptionError as exc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Provider credential encryption failed",
            ) from exc
        profile = EmbeddingProfile(
            id=profile_id,
            name=payload.name.strip(), base_url=payload.base_url.strip().rstrip("/"),
            model=payload.model.strip(), dimension=payload.dimension,
            credential_ref=credential_ref, backend=payload.backend,
            provider=payload.provider,
            api_key_ciphertext=ciphertext,
            created_by=identity.user.id,
        )
        session.add(profile)
        audit(
            session,
            "embedding_profile.create",
            "embedding_profile",
            profile.id,
            identity.user.id,
        )
        session.commit()
        session.refresh(profile)
        return _serialize_embedding_profile(profile, request.app.state.settings.data_dir)


@router.patch("/embedding-profiles/{profile_id}")
def update_embedding_profile(
    profile_id: str,
    payload: EmbeddingProfileUpdate,
    request: Request,
    _profile_lock: None = Depends(require_embedding_profile_mutation_lock),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        check_provider_config_rate_limit(identity.user.id)
        profile = session.get(EmbeddingProfile, profile_id)
        if not profile:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Embedding profile not found")
        if payload.clear_api_key and payload.api_key.strip():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "api_key and clear_api_key cannot be used together",
            )
        if payload.api_key.strip():
            require_browser_secret_transport(request)
        candidate_provider = payload.provider or profile.provider
        if candidate_provider not in {"openai", "tencent_multimodal"}:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "provider must be openai or tencent_multimodal",
            )
        candidate_base_url = (
            payload.base_url.strip().rstrip("/")
            if payload.base_url
            else profile.base_url
        )
        candidate_model = payload.model.strip() if payload.model else profile.model
        candidate_dimension = (
            payload.dimension if payload.dimension is not None else profile.dimension
        )
        vector_settings_changed = (
            candidate_base_url != profile.base_url
            or candidate_model != profile.model
            or candidate_dimension != profile.dimension
            or candidate_provider != profile.provider
        )
        if (
            candidate_base_url != profile.base_url.rstrip("/")
            and not payload.api_key.strip()
            and resolve_embedding_api_key(profile, request.app.state.settings.data_dir)
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "A replacement API key is required when changing Base URL",
            )
        if profile.is_active and vector_settings_changed:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Active embedding vector settings cannot be edited; "
                "create or edit an inactive profile and activate it",
            )
        if payload.name is not None:
            duplicate = session.exec(
                select(EmbeddingProfile).where(
                    EmbeddingProfile.name == payload.name.strip(),
                    EmbeddingProfile.id != profile.id,
                )
            ).first()
            if duplicate:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Embedding profile name already exists"
                )
            candidate_name = payload.name.strip()
        else:
            candidate_name = profile.name
        if payload.credential_ref is not None:
            try:
                candidate_credential_ref = validate_credential_ref(payload.credential_ref)
            except ValueError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        else:
            candidate_credential_ref = profile.credential_ref
        replacement_ciphertext: str | None = None
        if payload.clear_api_key:
            replacement_ciphertext = ""
        elif payload.api_key.strip():
            try:
                replacement_ciphertext = encrypt_secret(
                    request.app.state.settings.data_dir, payload.api_key.strip()
                )
            except CredentialEncryptionError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if vector_settings_changed:
            active_generation_ids = [
                repository.active_generation_id
                for repository in session.exec(select(Repository)).all()
                if repository.active_generation_id
                and repository.status in {"ready", "indexing"}
            ]
            if profile_contains_generation(
                request.app.state.settings,
                profile.id,
                active_generation_ids,
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Embedding profile still contains a repository's active generation",
                )
            try:
                delete_profile_collections(request.app.state.settings, profile.id)
            except Exception as exc:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Embedding vector collections could not be removed",
                ) from exc
        profile.name = candidate_name
        profile.base_url = candidate_base_url
        profile.model = candidate_model
        profile.dimension = candidate_dimension
        profile.provider = candidate_provider
        profile.credential_ref = candidate_credential_ref
        if replacement_ciphertext is not None:
            profile.api_key_ciphertext = replacement_ciphertext
        if profile.is_active and not resolve_embedding_api_key(
            profile, request.app.state.settings.data_dir
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cannot clear the only credential from the active embedding profile",
            )
        session.add(profile)
        action = (
            "embedding_profile.credential_clear"
            if payload.clear_api_key
            else "embedding_profile.credential_replace"
            if payload.api_key.strip()
            else "embedding_profile.update"
        )
        audit(
            session,
            action,
            "embedding_profile",
            profile.id,
            identity.user.id,
        )
        session.commit()
        session.refresh(profile)
        return _serialize_embedding_profile(profile, request.app.state.settings.data_dir)


@router.delete("/embedding-profiles/{profile_id}", status_code=204)
def delete_embedding_profile(
    profile_id: str,
    request: Request,
    _profile_lock: None = Depends(require_embedding_profile_mutation_lock),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        check_provider_config_rate_limit(identity.user.id)
        profile = session.get(EmbeddingProfile, profile_id)
        if not profile:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Embedding profile not found")
        if profile.is_active:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Active embedding profile cannot be deleted"
            )
        active_job = session.exec(
            select(IndexJob).where(col(IndexJob.status).in_(["queued", "running"]))
        ).first()
        if active_job:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Wait for current indexing jobs to finish before deleting an embedding profile",
            )
        active_generation_ids = [
            repository.active_generation_id
            for repository in session.exec(select(Repository)).all()
            if repository.active_generation_id
            and repository.status in {"ready", "indexing"}
        ]
        if profile_contains_generation(
            request.app.state.settings,
            profile.id,
            active_generation_ids,
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Embedding profile still contains a repository's active generation",
            )
        try:
            delete_profile_collections(request.app.state.settings, profile.id)
        except Exception as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Embedding vector collections could not be removed",
            ) from exc
        session.delete(profile)
        audit(
            session,
            "embedding_profile.delete",
            "embedding_profile",
            profile.id,
            identity.user.id,
        )
        session.commit()


@router.post("/embedding-profiles/probe")
def probe_embedding_profile(
    payload: EmbeddingProfileProbe,
    request: Request,
    _profile_lock: None = Depends(require_embedding_profile_mutation_lock),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        check_provider_config_rate_limit(identity.user.id)
        if payload.api_key.strip():
            require_browser_secret_transport(request)
        profile = session.get(EmbeddingProfile, payload.profile_id) if payload.profile_id else None
        if payload.profile_id and profile is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Embedding profile not found")
        try:
            credential_ref = (
                profile.credential_ref
                if profile
                else validate_credential_ref(payload.credential_ref)
                if payload.credential_ref.strip()
                else "browser-probe"
            )
            requested_base_url = payload.base_url.strip().rstrip("/")
            if (
                profile
                and not payload.api_key.strip()
                and requested_base_url != profile.base_url.rstrip("/")
            ):
                raise ValueError(
                    "A replacement API key is required to test a different Base URL"
                )
            api_key = payload.api_key.strip() or (
                resolve_embedding_api_key(profile, request.app.state.settings.data_dir)
                if profile
                else resolve_embedding_api_key(credential_ref)
            )
            if not api_key:
                raise ValueError(
                    "Embedding credential is not configured on the server: "
                    f"{embedding_credential_name(credential_ref)}"
                )
            if payload.provider not in {"openai", "tencent_multimodal"}:
                raise ValueError("provider must be openai or tencent_multimodal")
            probe_settings = replace(
                request.app.state.settings,
                embedding_mode=payload.provider,
                embedding_base_url=requested_base_url,
                embedding_api_key=api_key,
                embedding_model=payload.model.strip(),
            )
            dimension = EmbeddingClient(probe_settings).probe_dimension()
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        audit(
            session,
            "embedding_profile.test",
            "embedding_profile",
            profile.id if profile else "new",
            identity.user.id,
        )
        session.commit()
        return {"dimension": dimension}


@router.post("/embedding-profiles/{profile_id}/activate")
def activate_embedding_profile(
    profile_id: str,
    request: Request,
    _activation_locks: None = Depends(require_embedding_activation_locks),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        check_provider_config_rate_limit(identity.user.id)
        profile = session.get(EmbeddingProfile, profile_id)
        if not profile:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Embedding profile not found")
        active_job = session.exec(
            select(IndexJob).where(col(IndexJob.status).in_(["queued", "running"]))
        ).first()
        if active_job:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Wait for current indexing jobs to finish before switching embedding models",
            )
        try:
            profile_settings = settings_for_profile(request.app.state.settings, profile)
            returned_dimension = EmbeddingClient(profile_settings).probe_dimension()
            if returned_dimension != profile.dimension:
                raise ValueError(
                    f"configured dimension {profile.dimension}, "
                    f"provider returned {returned_dimension}"
                )
            VectorStore(profile_settings, namespace=profile.id)
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        try:
            request.app.state.external_sync.begin_embedding_switch()
        except RuntimeError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Wait for external source synchronization to finish before switching embeddings",
            ) from exc
        switch_committed = False
        try:
            for item in session.exec(
                select(EmbeddingProfile).where(EmbeddingProfile.is_active)
            ).all():
                item.is_active = False
                session.add(item)
            profile.is_active = True
            session.add(profile)
            jobs: list[IndexJob] = []
            repositories = session.exec(
                select(Repository)
                .where(col(Repository.status) != "archived")
                .order_by(Repository.id)
            ).all()
            for repository in repositories:
                try:
                    job = request.app.state.job_queue.add(
                        session,
                        JobRequest(
                            repository_id=repository.id,
                            created_by=identity.user.id,
                            message=f"Queued after switching to embedding model {profile.name}",
                            commit=repository.last_commit,
                        ),
                    )
                except ActiveIndexJobError as exc:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "An indexing job started while switching embedding models",
                    ) from exc
                if job is not None:
                    jobs.append(job)
            audit(
                session,
                "embedding_profile.activate",
                "embedding_profile",
                profile.id,
                identity.user.id,
            )
            job_ids = [job.id for job in jobs]
            response = _serialize_embedding_profile(
                profile, request.app.state.settings.data_dir
            )
            response["queued_jobs"] = len(job_ids)
            session.commit()
            switch_committed = True
        finally:
            if session.in_transaction():
                session.rollback()
            if not switch_committed:
                request.app.state.external_sync.end_embedding_switch()
    knowledge_error: Exception | None = None
    try:
        try:
            request.app.state.knowledge_search.refresh_embedding_context()
            response["knowledge_rebuild"] = (
                request.app.state.knowledge_search.rebuild_all()
            )
        except Exception as exc:
            knowledge_error = exc
        request.app.state.job_queue.submit(job_ids)
    finally:
        request.app.state.external_sync.end_embedding_switch()
    if knowledge_error is not None:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Embedding profile is already active and repository jobs were submitted, "
            "but the document and Wiki knowledge context could not be rebuilt",
        ) from knowledge_error
    return response


@router.post("/gitlab-sources", status_code=201)
def create_gitlab_source(payload: GitLabSourceCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        try:
            source = GitLabSource(
                name=payload.name.strip(),
                base_url=payload.base_url.strip().rstrip("/"),
                group_path=payload.group_path.strip().strip("/"),
                credential_ref=payload.credential_ref.strip(),
                poll_interval_seconds=payload.poll_interval_seconds,
                created_by=identity.user.id,
            )
            if session.exec(
                select(GitLabSource).where(GitLabSource.name == source.name)
            ).first():
                raise ValueError("GitLab source name already exists")
            if not source.name or not source.group_path:
                raise ValueError("GitLab source name and group are required")
            with GitLabClient(source.base_url, gitlab_credential(request, source.credential_ref)):
                pass
        except (ValueError, GitLabClientError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        session.add(source)
        audit(session, "gitlab_source.create", "gitlab_source", source.id, identity.user.id)
        session.commit()
        session.refresh(source)
        return serialize_gitlab_source(source)


@router.get("/gitlab-sources/{source_id}/projects")
def list_gitlab_projects(source_id: str, request: Request):
    with database(request) as session:
        require_admin(request, session)
        source = session.get(GitLabSource, source_id)
        if not source:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "GitLab source not found")
        try:
            token = gitlab_credential(request, source.credential_ref)
            with GitLabClient(source.base_url, token) as client:
                projects = client.list_group_projects(source.group_path)
        except (ValueError, GitLabClientError) as exc:
            source.last_error = str(exc)[:2000]
            source.last_checked_at = utc_now()
            session.add(source)
            session.commit()
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        source.last_error = ""
        source.last_checked_at = utc_now()
        session.add(source)
        session.commit()
        return [
            {
                "external_id": project.external_id,
                "path_with_namespace": project.path_with_namespace,
                "name": project.name,
                "description": project.description,
                "default_branch": project.default_branch,
                "web_url": project.web_url,
                "git_url": project.git_url,
            }
            for project in projects
        ]


@router.post("/gitlab-sources/{source_id}/import", status_code=201)
def import_gitlab_project(
    source_id: str, payload: GitLabProjectImport, request: Request
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        source = session.get(GitLabSource, source_id)
        if not source:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "GitLab source not found")
        if payload.visibility not in {"public", "private"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid visibility")
        duplicate = session.exec(
            select(Repository).where(
                Repository.source_id == source.id,
                Repository.external_project_id == payload.external_project_id,
            )
        ).first()
        if duplicate:
            raise HTTPException(status.HTTP_409_CONFLICT, "GitLab project already imported")
        try:
            token = gitlab_credential(request, source.credential_ref)
            with GitLabClient(source.base_url, token) as client:
                project = client.project(payload.external_project_id)
            name = validate_repository_name(project.name)
            if session.exec(select(Repository).where(Repository.name == name)).first():
                raise HTTPException(status.HTTP_409_CONFLICT, "Repository name already exists")
        except HTTPException:
            raise
        except (ValueError, GitLabClientError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        repository = Repository(
            name=name,
            description=project.description,
            git_url=project.git_url,
            branch=project.default_branch,
            visibility=payload.visibility,
            source_id=source.id,
            external_project_id=project.external_id,
            created_by=identity.user.id,
        )
        session.add(repository)
        audit(session, "repository.import_gitlab", "repository", repository.id, identity.user.id)
        session.commit()
        session.refresh(repository)
        return serialize_repository(repository)


@router.get("/repositories")
def list_repositories(request: Request):
    with database(request) as session:
        identity = resolve_identity(request, session)
        if identity is None and not request.app.state.settings.allow_anonymous_search:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
        scope = authorization_scope(
            request, session, identity.user if identity else None, allow_anonymous=True
        )
        if identity and is_admin_role(identity.user.role):
            statement = select(Repository).order_by(col(Repository.created_at).desc())
            if scope.space_ids:
                statement = statement.where(col(Repository.space_id).in_(scope.space_ids))
            repositories = session.exec(statement).all()
        else:
            repositories = request.app.state.retriever.allowed_repositories(
                None, authorization_scope=scope
            )
        return [serialize_repository(repo) for repo in repositories]


@router.post("/repositories", status_code=201)
def create_repository(payload: RepositoryCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        scope = authorization_scope(request, session, identity.user)
        require_space(session, scope, payload.space_id, "manage")
        try:
            name = validate_repository_name(payload.name)
            git_url = validate_public_git_url(
                payload.git_url, request.app.state.settings.allowed_git_hosts
            )
            branch = validate_git_branch(payload.branch)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if payload.visibility not in {"public", "private"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid visibility")
        if session.exec(select(Repository).where(Repository.name == name)).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Repository name already exists")
        repo = Repository(
            name=name, description=payload.description.strip(), git_url=git_url,
            branch=branch, visibility=payload.visibility,
            space_id=payload.space_id,
            license_name=payload.license_name.strip(), license_url=payload.license_url.strip(),
            created_by=identity.user.id,
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)
        audit(session, "repository.create", "repository", repo.id, identity.user.id)
        session.commit()
        return serialize_repository(repo)


@router.delete("/repositories/{repository_id}", status_code=204)
def archive_repository(repository_id: str, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        repo = session.get(Repository, repository_id)
        if not repo:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
        repo.status = "archived"
        repo.visibility = "private"
        session.add(repo)
        audit(session, "repository.archive", "repository", repo.id, identity.user.id)
        session.commit()


@router.post("/repositories/{repository_id}/sync", status_code=202)
def queue_sync(
    repository_id: str,
    request: Request,
    _schedule_lock: None = Depends(require_index_job_schedule_lock),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        repo = session.get(Repository, repository_id)
        if not repo:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
        try:
            job = request.app.state.job_queue.add(
                session,
                JobRequest(repository_id=repo.id, created_by=identity.user.id),
            )
        except ActiveIndexJobError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Repository already has an active job"
            ) from exc
        audit(session, "repository.sync", "repository", repo.id, identity.user.id)
        session.commit()
        if job is None:
            raise RuntimeError("Index job queue returned no job")
        session.refresh(job)
        job_id = job.id
        response = serialize_job(job)
    request.app.state.job_queue.submit((job_id,))
    return response


@router.post("/search")
def search(payload: SearchRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    with database(request) as session:
        identity = resolve_identity(request, session)
        if identity is None and not request.app.state.settings.allow_anonymous_search:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
        scope = authorization_scope(
            request, session, identity.user if identity else None, allow_anonymous=True
        )
        rate_key = identity.user.id if identity else client_ip
        limiter.check(f"search:{rate_key}", 120 if identity else 30)
        try:
            return request.app.state.retriever.search(
                payload.query,
                identity.user if identity else None,
                payload.repository_ids or None,
                payload.languages or None,
                payload.path_prefix,
                payload.limit,
                authorization_scope=scope,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/chat/status")
def chat_status(request: Request):
    with database(request) as session:
        provider = session.exec(select(LlmProvider).where(LlmProvider.is_active)).first()
        if provider:
            key = decrypt_api_key(request.app.state.settings.data_dir, provider)
            service = ChatService(request.app.state.settings, request.app.state.retriever,
                                  type("Provider", (), {"base_url": provider.base_url,
                                                         "api_key": key,
                                                         "model": provider.model})())
            return {"enabled": service.enabled, "model": provider.model}
    service = ChatService(request.app.state.settings, request.app.state.retriever)
    return {"enabled": service.enabled, "model": request.app.state.settings.llm_model}


def _serialize_llm_provider(provider: LlmProvider) -> dict:
    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "model": provider.model,
        "models": json.loads(provider.models_json or "[]"),
        "is_active": provider.is_active,
        "api_key_configured": bool(provider.api_key_ciphertext),
        "last_synced_at": provider.last_synced_at,
    }


@router.get("/llm/providers")
def list_llm_providers(request: Request):
    with database(request) as session:
        require_admin(request, session)
        return [_serialize_llm_provider(item) for item in session.exec(
            select(LlmProvider).order_by(col(LlmProvider.created_at).desc())
        ).all()]


@router.post("/llm/providers", status_code=201)
def create_llm_provider(
    payload: LlmProviderCreate,
    request: Request,
    _provider_lock: None = Depends(require_llm_provider_mutation_lock),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        check_provider_config_rate_limit(identity.user.id)
        if payload.api_key.strip():
            require_browser_secret_transport(request)
        try:
            base_url = normalize_base_url(payload.base_url)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        name = (payload.name or new_provider_name(base_url)).strip()
        if session.exec(select(LlmProvider).where(LlmProvider.name == name)).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "provider name already exists")
        provider = LlmProvider(
            name=name, base_url=base_url, model=payload.model.strip(),
            api_key_ciphertext=encrypt_api_key(
                request.app.state.settings.data_dir, payload.api_key.strip()
            ),
            models_json=json.dumps(payload.models, ensure_ascii=False),
            created_by=identity.user.id,
        )
        session.add(provider)
        audit(
            session,
            "llm_provider.create",
            "llm_provider",
            provider.id,
            identity.user.id,
        )
        session.commit()
        session.refresh(provider)
        return _serialize_llm_provider(provider)


@router.patch("/llm/providers/{provider_id}")
def update_llm_provider(
    provider_id: str,
    payload: LlmProviderUpdate,
    request: Request,
    _provider_lock: None = Depends(require_llm_provider_mutation_lock),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        check_provider_config_rate_limit(identity.user.id)
        provider = session.get(LlmProvider, provider_id)
        if not provider:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "provider not found")
        if payload.clear_api_key and payload.api_key.strip():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "api_key and clear_api_key cannot be used together",
            )
        if payload.api_key.strip():
            require_browser_secret_transport(request)
        if payload.name is not None:
            duplicate = session.exec(
                select(LlmProvider).where(
                    LlmProvider.name == payload.name.strip(),
                    LlmProvider.id != provider.id,
                )
            ).first()
            if duplicate:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "provider name already exists"
                )
            provider.name = payload.name.strip()
        if payload.base_url is not None:
            try:
                candidate_base_url = normalize_base_url(payload.base_url)
            except ValueError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
            if (
                candidate_base_url != provider.base_url
                and provider.api_key_ciphertext
                and not payload.api_key.strip()
            ):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "A replacement API key is required when changing Base URL",
                )
            provider.base_url = candidate_base_url
        if payload.model is not None:
            provider.model = payload.model.strip()
        if payload.models is not None:
            provider.models_json = json.dumps(payload.models, ensure_ascii=False)
        if payload.clear_api_key:
            if provider.is_active:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Cannot clear the API key from the active LLM provider",
                )
            provider.api_key_ciphertext = ""
        elif payload.api_key.strip():
            provider.api_key_ciphertext = encrypt_api_key(
                request.app.state.settings.data_dir, payload.api_key.strip()
            )
        session.add(provider)
        action = (
            "llm_provider.credential_clear"
            if payload.clear_api_key
            else "llm_provider.credential_replace"
            if payload.api_key.strip()
            else "llm_provider.update"
        )
        audit(
            session,
            action,
            "llm_provider",
            provider.id,
            identity.user.id,
        )
        session.commit()
        session.refresh(provider)
        return _serialize_llm_provider(provider)


@router.delete("/llm/providers/{provider_id}", status_code=204)
def delete_llm_provider(
    provider_id: str,
    request: Request,
    _provider_lock: None = Depends(require_llm_provider_mutation_lock),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        check_provider_config_rate_limit(identity.user.id)
        provider = session.get(LlmProvider, provider_id)
        if not provider:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "provider not found")
        if provider.is_active:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Active LLM provider cannot be deleted"
            )
        session.delete(provider)
        audit(
            session,
            "llm_provider.delete",
            "llm_provider",
            provider.id,
            identity.user.id,
        )
        session.commit()


@router.post("/llm/providers/{provider_id}/test")
def test_llm_provider(
    provider_id: str,
    request: Request,
    _provider_lock: None = Depends(require_llm_provider_mutation_lock),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        check_provider_config_rate_limit(identity.user.id)
        provider = session.get(LlmProvider, provider_id)
        if not provider:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "provider not found")
        key = decrypt_api_key(request.app.state.settings.data_dir, provider)
        if not key:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "provider API key is not configured",
            )
        try:
            models = sync_models(provider.base_url, key)
        except (LlmProviderError, ValueError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        provider.models_json = json.dumps(models, ensure_ascii=False)
        provider.last_synced_at = utc_now()
        session.add(provider)
        audit(
            session,
            "llm_provider.test",
            "llm_provider",
            provider.id,
            identity.user.id,
        )
        session.commit()
        return {"models": models, "count": len(models)}


@router.post("/llm/providers/sync")
def sync_llm_provider(
    payload: LlmProviderSyncRequest,
    request: Request,
    _provider_lock: None = Depends(require_llm_provider_mutation_lock),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        check_provider_config_rate_limit(identity.user.id)
        if payload.api_key.strip():
            require_browser_secret_transport(request)
        provider = session.get(LlmProvider, payload.provider_id) if payload.provider_id else None
        if payload.provider_id and provider is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "provider not found")
        try:
            requested_base_url = normalize_base_url(payload.base_url)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if (
            provider
            and not payload.api_key.strip()
            and requested_base_url != provider.base_url
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "A replacement API key is required to test a different Base URL",
            )
        key = payload.api_key.strip() or (
            decrypt_api_key(request.app.state.settings.data_dir, provider)
            if provider
            else ""
        )
        if not key:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "provider API key is not configured",
            )
        try:
            models = sync_models(requested_base_url, key)
        except (LlmProviderError, ValueError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        audit(
            session,
            "llm_provider.test",
            "llm_provider",
            provider.id if provider else "new",
            identity.user.id,
        )
        session.commit()
        return {"models": models, "count": len(models)}


@router.post("/llm/providers/{provider_id}/activate")
def activate_llm_provider(
    provider_id: str,
    request: Request,
    _provider_lock: None = Depends(require_llm_provider_mutation_lock),
):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        check_provider_config_rate_limit(identity.user.id)
        provider = session.get(LlmProvider, provider_id)
        if not provider:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "provider not found")
        key = decrypt_api_key(request.app.state.settings.data_dir, provider)
        if not key:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "provider API key is not configured",
            )
        for item in session.exec(select(LlmProvider).where(LlmProvider.is_active)).all():
            item.is_active = False
            session.add(item)
        provider.is_active = True
        session.add(provider)
        audit(
            session,
            "llm_provider.activate",
            "llm_provider",
            provider.id,
            identity.user.id,
        )
        session.commit()
        return _serialize_llm_provider(provider)


@router.post("/chat")
def chat(payload: ChatRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    with database(request) as session:
        identity = resolve_identity(request, session)
        if identity is None and not request.app.state.settings.allow_anonymous_chat:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
        scope = authorization_scope(
            request, session, identity.user if identity else None, allow_anonymous=True
        )
        rate_key = identity.user.id if identity else client_ip
        limiter.check(f"chat:{rate_key}", 20 if identity else 5)
        provider = session.exec(select(LlmProvider).where(LlmProvider.is_active)).first()
        provider_config = None
        if provider:
            provider_config = type("Provider", (), {
                "base_url": provider.base_url,
                "api_key": decrypt_api_key(request.app.state.settings.data_dir, provider),
                "model": provider.model,
            })()
        service = ChatService(
            request.app.state.settings, request.app.state.retriever, provider_config
        )
        try:
            return service.ask(
                payload.question,
                identity.user if identity else None,
                payload.repository_ids or None,
                [turn.model_dump() for turn in payload.history],
                authorization_scope=scope,
            )
        except ChatUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _owned_chat_session(session: Session, session_id: str, user_id: str) -> ChatSession:
    conversation = session.exec(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
        )
    ).first()
    if not conversation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat session not found")
    return conversation


def _serialize_chat_session(conversation: ChatSession) -> dict:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "repository_ids": json.loads(conversation.repository_ids_json or "[]"),
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def _serialize_chat_message(message: ChatMessage) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "citations": json.loads(message.citations_json or "[]"),
        "created_at": message.created_at,
    }


def _serialize_user_memory(memory: UserMemory) -> dict:
    return {
        "id": memory.id,
        "kind": memory.kind,
        "content": memory.content,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }


@contextmanager
def locked_chat_session_request(
    request: Request,
    session_id: str,
) -> Iterator[Connection]:
    """Authorize, lock one chat session, then release the account row lock."""
    with request.app.state.engine.connect() as connection:
        lock_name = ""
        try:
            with Session(connection) as auth_session:
                identity = require_identity(request, auth_session, lock_user=False)
                require_csrf(request, identity)
                lock_name = acquire_chat_session_lock(connection, session_id)
                auth_session.rollback()
                connection.commit()
            with Session(connection) as verification_session:
                identity = require_identity(
                    request, verification_session, lock_user=False
                )
                require_csrf(request, identity)
            yield connection
        finally:
            if lock_name:
                release_chat_session_lock(connection, lock_name)
                connection.commit()


@contextmanager
def locked_member_lifecycle_request(request: Request) -> Iterator[Connection]:
    with database(request) as session:
        identity = require_admin(request, session, lock_user=False)
        require_csrf(request, identity)
    try:
        with member_lifecycle_lock(request.app.state.engine) as connection:
            yield connection
    except MemberLifecycleLockError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@contextmanager
def locked_user_chat_sessions(
    session: Session,
    connection: Connection,
    user_id: str,
) -> Iterator[None]:
    session_ids = session.exec(
        select(ChatSession.id)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.id)
    ).all()
    lock_names: list[str] = []
    try:
        try:
            for session_id in session_ids:
                lock_names.append(acquire_chat_session_lock(connection, session_id))
        except ChatSessionLockError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        session.commit()
        yield
    finally:
        for lock_name in reversed(lock_names):
            release_chat_session_lock(connection, lock_name)


@router.get("/memories")
def list_user_memories(request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        memories = session.exec(
            select(UserMemory)
            .where(UserMemory.user_id == identity.user.id)
            .order_by(col(UserMemory.updated_at).desc())
        ).all()
        return [_serialize_user_memory(item) for item in memories]


@router.post("/memories", status_code=201)
def create_user_memory(payload: UserMemoryCreate, request: Request):
    allowed_kinds = {"preference", "project", "environment", "constraint", "fact"}
    with database(request) as session:
        identity = require_identity(request, session)
        require_csrf(request, identity)
        kind = payload.kind.strip().lower()
        content = payload.content.strip()
        if kind not in allowed_kinds:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid memory kind"
            )
        if contains_secret(content):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Secrets and credentials cannot be stored as memory",
            )
        content_hash = digest_secret(content)
        duplicate = session.exec(
            select(UserMemory).where(
                UserMemory.user_id == identity.user.id,
                UserMemory.kind == kind,
                UserMemory.content_hash == content_hash,
            )
        ).first()
        if duplicate:
            raise HTTPException(status.HTTP_409_CONFLICT, "Memory already exists")
        memory = UserMemory(
            user_id=identity.user.id,
            kind=kind,
            content=content,
            content_hash=content_hash,
        )
        session.add(memory)
        audit(
            session,
            "memory.create",
            "user_memory",
            memory.id,
            identity.user.id,
        )
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            duplicate = session.exec(
                select(UserMemory).where(
                    UserMemory.user_id == identity.user.id,
                    UserMemory.kind == kind,
                    UserMemory.content_hash == content_hash,
                )
            ).first()
            if duplicate:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Memory already exists"
                ) from exc
            raise
        session.refresh(memory)
        return _serialize_user_memory(memory)


@router.delete("/memories/{memory_id}", status_code=204)
def delete_user_memory(memory_id: str, request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        require_csrf(request, identity)
        memory = session.exec(
            select(UserMemory).where(
                UserMemory.id == memory_id,
                UserMemory.user_id == identity.user.id,
            )
        ).first()
        if not memory:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
        audit(
            session,
            "memory.delete",
            "user_memory",
            memory.id,
            identity.user.id,
        )
        session.delete(memory)
        session.commit()


@router.post("/chat/sessions", status_code=201)
def create_chat_session(payload: ChatSessionCreate, request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        require_csrf(request, identity)
        scope = authorization_scope(request, session, identity.user)
        repository_ids = sorted(set(payload.repository_ids))
        if not set(repository_ids).issubset(scope.repository_ids):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Chat session references an inaccessible repository",
            )
        normalized_title = payload.title.strip()[:200] or "新对话"
        repository_ids_json = json.dumps(repository_ids)
        if payload.request_id:
            existing = session.exec(
                select(ChatSession).where(
                    ChatSession.user_id == identity.user.id,
                    ChatSession.request_id == payload.request_id,
                )
            ).first()
            if existing:
                if (
                    existing.title != normalized_title
                    or existing.repository_ids_json != repository_ids_json
                ):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "request_id was already used for another chat session",
                    )
                return _serialize_chat_session(existing)
        conversation = ChatSession(
            user_id=identity.user.id,
            request_id=payload.request_id,
            title=normalized_title,
            repository_ids_json=repository_ids_json,
        )
        session.add(conversation)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if not payload.request_id:
                raise
            existing = session.exec(
                select(ChatSession).where(
                    ChatSession.user_id == identity.user.id,
                    ChatSession.request_id == payload.request_id,
                )
            ).first()
            if existing and (
                existing.title == normalized_title
                and existing.repository_ids_json == repository_ids_json
            ):
                return _serialize_chat_session(existing)
            if existing:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "request_id was already used for another chat session",
                ) from exc
            raise
        session.refresh(conversation)
        return _serialize_chat_session(conversation)


@router.get("/chat/sessions")
def list_chat_sessions(request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        conversations = session.exec(
            select(ChatSession)
            .where(ChatSession.user_id == identity.user.id)
            .order_by(col(ChatSession.updated_at).desc())
        ).all()
        return [_serialize_chat_session(item) for item in conversations]


@router.get("/chat/sessions/{session_id}")
def get_chat_session(session_id: str, request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        conversation = _owned_chat_session(session, session_id, identity.user.id)
        messages = session.exec(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == conversation.id,
                ChatMessage.user_id == identity.user.id,
            )
            .order_by(col(ChatMessage.sequence))
        ).all()
        return _serialize_chat_session(conversation) | {
            "messages": [_serialize_chat_message(item) for item in messages]
        }


@router.delete("/chat/sessions/{session_id}", status_code=204)
def delete_chat_session(session_id: str, request: Request):
    try:
        with locked_chat_session_request(request, session_id) as connection:
            with Session(connection) as session:
                identity = require_identity(request, session, lock_user=False)
                require_csrf(request, identity)
                conversation = _owned_chat_session(
                    session, session_id, identity.user.id
                )
                messages = session.exec(
                    select(ChatMessage).where(
                        ChatMessage.session_id == conversation.id,
                        ChatMessage.user_id == identity.user.id,
                    )
                ).all()
                for message in messages:
                    session.delete(message)
                session.flush()
                session.delete(conversation)
                session.commit()
    except ChatSessionLockError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/chat/sessions/{session_id}/messages")
def create_chat_message(
    session_id: str, payload: ChatMessageCreate, request: Request
):
    client_ip = request.client.host if request.client else "unknown"
    with database(request) as session:
        identity = require_identity(request, session, lock_user=False)
        require_csrf(request, identity)
        limiter.check(f"chat:{identity.user.id or client_ip}", 20)
    try:
        with locked_chat_session_request(request, session_id) as connection:
            with Session(connection) as session:
                identity = require_identity(request, session, lock_user=False)
                require_csrf(request, identity)
                scope = authorization_scope(request, session, identity.user)
                conversation = _owned_chat_session(
                    session, session_id, identity.user.id
                )
                requested_repositories = set(
                    json.loads(conversation.repository_ids_json or "[]")
                )
                if not requested_repositories.issubset(scope.repository_ids):
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN,
                        "Chat session contains a repository that is no longer accessible",
                    )
                normalized_question = payload.question.strip()
                if payload.request_id:
                    existing_user_message = session.exec(
                        select(ChatMessage).where(
                            ChatMessage.session_id == conversation.id,
                            ChatMessage.user_id == identity.user.id,
                            ChatMessage.request_id == payload.request_id,
                        )
                    ).first()
                    if existing_user_message:
                        if existing_user_message.content != normalized_question:
                            raise HTTPException(
                                status.HTTP_409_CONFLICT,
                                "request_id was already used for another question",
                            )
                        existing_answer = session.exec(
                            select(ChatMessage).where(
                                ChatMessage.session_id == conversation.id,
                                ChatMessage.user_id == identity.user.id,
                                ChatMessage.sequence == existing_user_message.sequence + 1,
                                ChatMessage.role == "assistant",
                            )
                        ).first()
                        if not existing_answer:
                            raise HTTPException(
                                status.HTTP_409_CONFLICT,
                                "Chat request is still being finalized",
                            )
                        return {
                            "answer": existing_answer.content,
                            "citations": json.loads(
                                existing_answer.citations_json or "[]"
                            ),
                        }
                stored_messages = session.exec(
                    select(ChatMessage)
                    .where(
                        ChatMessage.session_id == conversation.id,
                        ChatMessage.user_id == identity.user.id,
                    )
                    .order_by(col(ChatMessage.sequence).desc())
                    .limit(6)
                ).all()
                history = [
                    {"role": item.role, "content": item.content}
                    for item in reversed(stored_messages)
                ]
                memories = session.exec(
                    select(UserMemory)
                    .where(UserMemory.user_id == identity.user.id)
                    .order_by(col(UserMemory.updated_at).desc())
                    .limit(20)
                ).all()
                provider = session.exec(
                    select(LlmProvider).where(LlmProvider.is_active)
                ).first()
                provider_config = None
                if provider:
                    provider_config = type(
                        "Provider",
                        (),
                        {
                            "base_url": provider.base_url,
                            "api_key": decrypt_api_key(
                                request.app.state.settings.data_dir, provider
                            ),
                            "model": provider.model,
                        },
                    )()
                service = ChatService(
                    request.app.state.settings,
                    request.app.state.retriever,
                    provider_config,
                )
                try:
                    result = service.ask(
                        payload.question,
                        identity.user,
                        json.loads(conversation.repository_ids_json or "[]") or None,
                        history,
                        [item.content for item in memories],
                        authorization_scope=scope,
                    )
                except ChatUnavailableError as exc:
                    raise HTTPException(
                        status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)
                    ) from exc
                except ValueError as exc:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
                    ) from exc
                _store_chat_exchange(
                    session,
                    conversation.id,
                    identity.user.id,
                    payload.question,
                    result,
                    payload.request_id,
                )
                session.commit()
                return result
    except ChatSessionLockError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


def _store_chat_exchange(
    session: Session,
    session_id: str,
    user_id: str,
    question: str,
    result: dict,
    request_id: str | None = None,
) -> None:
    conversation = session.exec(
        select(ChatSession)
        .where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if not conversation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat session not found")
    user_sequence = conversation.message_count + 1
    assistant_sequence = conversation.message_count + 2
    session.add_all(
        [
            ChatMessage(
                session_id=conversation.id,
                user_id=user_id,
                role="user",
                sequence=user_sequence,
                request_id=request_id,
                content=question.strip(),
            ),
            ChatMessage(
                session_id=conversation.id,
                user_id=user_id,
                role="assistant",
                sequence=assistant_sequence,
                content=str(result["answer"]),
                citations_json=json.dumps(result["citations"], ensure_ascii=False),
            ),
        ]
    )
    conversation.message_count = assistant_sequence
    conversation.updated_at = utc_now()
    session.add(conversation)


@router.get("/repositories/{repository_id}/tree")
def get_tree(
    repository_id: str, request: Request, path: str = Query(default="", max_length=1000),
):
    with database(request) as session:
        identity = resolve_identity(request, session)
        if identity is None and not request.app.state.settings.allow_anonymous_search:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
        scope = authorization_scope(
            request, session, identity.user if identity else None, allow_anonymous=True
        )
        repositories = {
            repo.id: repo
            for repo in request.app.state.retriever.allowed_repositories(
                None, authorization_scope=scope
            )
        }
        repository = repositories.get(repository_id)
        if not repository:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "repository is not accessible")
        root = Path(repository.local_path).resolve()
        target = (root / path).resolve() if path else root
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "path escapes the repository root"
            ) from exc
        if not target.is_dir():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "directory not found")
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if child.name.startswith(".") and child.name != ".github":
                continue
            relative = child.relative_to(root).as_posix()
            entries.append({
                "name": child.name,
                "path": relative,
                "type": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            })
            if len(entries) >= 500:
                break
        return {"path": path, "entries": entries}


@router.get("/stats")
def get_stats(request: Request):
    with database(request) as session:
        identity = resolve_identity(request, session)
        if identity is None and not request.app.state.settings.allow_anonymous_search:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
        scope = authorization_scope(
            request, session, identity.user if identity else None, allow_anonymous=True
        )
        repositories = request.app.state.retriever.allowed_repositories(
            None, authorization_scope=scope
        )
        language_counts: dict[str, int] = {}
        for repo in repositories:
            if not repo.active_generation_id:
                continue
            rows = session.exec(
                select(CodeChunkRecord.language, func.count())
                .where(CodeChunkRecord.generation_id == repo.active_generation_id)
                .group_by(CodeChunkRecord.language)
            ).all()
            for language, count in rows:
                language_counts[language] = language_counts.get(language, 0) + count
        ranked = sorted(language_counts.items(), key=lambda kv: kv[1], reverse=True)[:12]
        languages = [{"language": lang or "unknown", "chunks": count} for lang, count in ranked]
        return {
            "repository_count": len(repositories),
            "ready_count": sum(1 for repo in repositories if repo.status == "ready"),
            "chunk_total": sum(repo.chunk_count for repo in repositories),
            "languages": languages,
        }


@router.get("/repositories/{repository_id}/file")
def get_file(
    repository_id: str, request: Request, path: str = Query(..., max_length=1000),
    start_line: int = 1, end_line: int = 200,
):
    with database(request) as session:
        identity = resolve_identity(request, session)
        if identity is None and not request.app.state.settings.allow_anonymous_search:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
        scope = authorization_scope(
            request, session, identity.user if identity else None, allow_anonymous=True
        )
        try:
            return request.app.state.retriever.get_file(
                repository_id,
                path,
                None,
                start_line,
                end_line,
                authorization_scope=scope,
            )
        except PermissionError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found") from exc


@router.get("/index-jobs")
def list_jobs(request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        scope = authorization_scope(request, session, identity.user)
        statement = select(IndexJob)
        if not is_admin_role(identity.user.role):
            repository_ids = list(scope.repository_ids)
            if not repository_ids:
                return []
            statement = statement.where(
                col(IndexJob.repository_id).in_(repository_ids)
            )
        jobs = session.exec(
            statement.order_by(col(IndexJob.created_at).desc()).limit(100)
        ).all()
        return [serialize_job(job) for job in jobs]


@router.get("/members")
def list_members(request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        statement = select(User)
        if not is_owner_role(identity.user.role):
            statement = statement.where(
                (User.id == identity.user.id) | (User.role == MEMBER_ROLE)
            )
        users = session.exec(statement.order_by(col(User.created_at))).all()
        return [public_user(user) for user in users]


@router.patch("/members/{user_id}")
def update_member(user_id: str, payload: MemberUpdate, request: Request):
    with locked_member_lifecycle_request(request) as connection:
        with Session(connection) as session:
            actor = require_admin(request, session, lock_user=False)
            require_csrf(request, actor)
            locked_users = session.exec(
                select(User)
                .where(col(User.id).in_(sorted({actor.user.id, user_id})))
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            users_by_id = {item.id: item for item in locked_users}
            identity_user = users_by_id.get(actor.user.id)
            user = users_by_id.get(user_id)
            if not identity_user or not identity_user.is_active:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Authentication required"
                )
            if not user:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
            if not is_admin_role(identity_user.role):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "Administrator role required"
                )
            if not can_manage_role(identity_user.role, user.role):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Only an owner can manage administrators",
                )
            if payload.role is not None:
                if payload.role not in ASSIGNABLE_ROLES:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid role"
                    )
                if not can_assign_role(identity_user.role, payload.role):
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN,
                        "Only an owner can assign administrator roles",
                    )
            removes_active_owner = (
                user.role == OWNER_ROLE
                and user.is_active
                and (
                    (payload.role is not None and payload.role != OWNER_ROLE)
                    or payload.is_active is False
                )
            )
            if removes_active_owner:
                active_owner_count = session.exec(
                    select(func.count()).select_from(User).where(
                        User.role == OWNER_ROLE,
                        User.is_active,
                    )
                ).one()
                if active_owner_count <= 1:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "The last active owner cannot be changed",
                    )
            if payload.role is not None:
                user.role = payload.role
            if payload.is_active is not None:
                user.is_active = payload.is_active
                if not payload.is_active:
                    browser_sessions = session.exec(
                        select(UserSession)
                        .where(UserSession.user_id == user.id)
                        .with_for_update()
                    ).all()
                    for browser_session in browser_sessions:
                        session.delete(browser_session)
            session.add(user)
            audit(session, "member.update", "user", user.id, identity_user.id)
            session.commit()
            result = public_user(user)
            if payload.is_active is False:
                with locked_user_chat_sessions(session, connection, user.id):
                    pass
            return result


@router.delete("/members/{user_id}", status_code=204)
def delete_member(user_id: str, request: Request):
    with locked_member_lifecycle_request(request) as connection:
        with Session(connection) as session:
            actor = require_admin(request, session, lock_user=False)
            require_csrf(request, actor)
            locked_users = session.exec(
                select(User)
                .where(col(User.id).in_(sorted({actor.user.id, user_id})))
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            users_by_id = {item.id: item for item in locked_users}
            identity_user = users_by_id.get(actor.user.id)
            user = users_by_id.get(user_id)
            if not identity_user or not identity_user.is_active:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Authentication required"
                )
            if not user:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
            if not is_admin_role(identity_user.role):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "Administrator role required"
                )
            if user.id == identity_user.id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete yourself")
            if not can_manage_role(identity_user.role, user.role):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Only an owner can manage administrators",
                )
            if user.role == OWNER_ROLE and user.is_active:
                active_owner_count = session.exec(
                    select(func.count()).select_from(User).where(
                        User.role == OWNER_ROLE,
                        User.is_active,
                    )
                ).one()
                if active_owner_count <= 1:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "The last active owner cannot be deleted",
                    )
            actor_id = identity_user.id
            user.is_active = False
            session.add(user)
            for browser_session in session.exec(
                select(UserSession)
                        .where(UserSession.user_id == user.id)
                        .with_for_update()
            ).all():
                session.delete(browser_session)
            session.commit()

        with Session(connection) as session:
          with locked_user_chat_sessions(session, connection, user_id):
            identity_user = session.get(User, actor_id)
            user = session.get(User, user_id)
            if not identity_user or not identity_user.is_active:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Authentication required"
                )
            if not user:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
            for model in (
                GitLabSource,
                GitHubSource,
                DocumentCollection,
                Document,
                ExternalSource,
                WikiPage,
                EmbeddingProfile,
                LlmProvider,
                Repository,
                IndexJob,
            ):
                for item in session.exec(
                    select(model).where(model.created_by == user.id)
                ).all():
                    item.created_by = identity_user.id
                    session.add(item)

            conversations = session.exec(
                select(ChatSession).where(ChatSession.user_id == user.id)
            ).all()
            conversation_ids = [conversation.id for conversation in conversations]
            if conversation_ids:
                messages = session.exec(
                    select(ChatMessage).where(
                        col(ChatMessage.session_id).in_(conversation_ids)
                    )
                ).all()
                for message in messages:
                    session.delete(message)
                session.flush()
            for conversation in conversations:
                session.delete(conversation)

            for memory in session.exec(
                select(UserMemory).where(UserMemory.user_id == user.id)
            ).all():
                session.delete(memory)
            for browser_session in session.exec(
                select(UserSession)
                        .where(UserSession.user_id == user.id)
                        .with_for_update()
            ).all():
                session.delete(browser_session)
            for access in session.exec(
                select(RepositoryAccess).where(RepositoryAccess.user_id == user.id)
            ).all():
                session.delete(access)
            for token in session.exec(
                select(ApiToken).where(ApiToken.created_by == user.id)
            ).all():
                session.delete(token)

            audit(session, "member.delete", "user", user.id, actor_id)
            session.flush()
            session.delete(user)
            session.commit()


@router.post("/members", status_code=201)
def create_member(payload: MemberCreate, request: Request):
    with locked_member_lifecycle_request(request) as connection:
      with Session(connection) as session:
        identity = require_admin(request, session, lock_user=False)
        require_csrf(request, identity)
        email = payload.email.strip().lower()
        if payload.role not in ASSIGNABLE_ROLES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid role")
        if not can_assign_role(identity.user.role, payload.role):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only an owner can assign administrator roles",
            )
        if session.exec(select(User).where(User.email == email)).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Email already exists")
        user = User(
            email=email, display_name=payload.display_name.strip(),
            password_hash=hash_password(payload.password), role=payload.role,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        audit(session, "member.create", "user", user.id, identity.user.id)
        session.commit()
        return public_user(user)


@router.put("/members/{user_id}/repositories/{repository_id}", status_code=204)
def grant_repository(user_id: str, repository_id: str, request: Request):
    with locked_member_lifecycle_request(request) as connection:
      with Session(connection) as session:
        identity = require_admin(request, session, lock_user=False)
        require_csrf(request, identity)
        user = session.exec(
            select(User).where(User.id == user_id).with_for_update()
        ).first()
        if not user or not session.get(Repository, repository_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User or repository not found")
        if not can_manage_role(identity.user.role, user.role):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only an owner can manage administrator access",
            )
        existing = session.exec(select(RepositoryAccess).where(
            RepositoryAccess.user_id == user_id,
            RepositoryAccess.repository_id == repository_id,
        )).first()
        if not existing:
            session.add(RepositoryAccess(user_id=user_id, repository_id=repository_id))
            audit(session, "repository.grant", "repository", repository_id, identity.user.id)
            session.commit()


@router.delete("/members/{user_id}/repositories/{repository_id}", status_code=204)
def revoke_repository(user_id: str, repository_id: str, request: Request):
    with locked_member_lifecycle_request(request) as connection:
      with Session(connection) as session:
        identity = require_admin(request, session, lock_user=False)
        require_csrf(request, identity)
        user = session.exec(
            select(User).where(User.id == user_id).with_for_update()
        ).first()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        if not can_manage_role(identity.user.role, user.role):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only an owner can manage administrator access",
            )
        access = session.exec(select(RepositoryAccess).where(
            RepositoryAccess.user_id == user_id,
            RepositoryAccess.repository_id == repository_id,
        )).first()
        if not access:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Access not found")
        session.delete(access)
        audit(session, "repository.revoke", "repository", repository_id, identity.user.id)
        session.commit()


@router.get("/tokens")
def list_tokens(request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        statement = select(ApiToken)
        if not is_owner_role(identity.user.role):
            statement = statement.where(ApiToken.created_by == identity.user.id)
        tokens = session.exec(statement.order_by(col(ApiToken.created_at).desc())).all()
        return [serialize_token(token) for token in tokens]


@router.post("/tokens", status_code=201)
def create_token(payload: TokenCreate, request: Request):
    allowed_scopes = {"search", "read", "status"}
    scopes = sorted(set(payload.scopes))
    if not scopes or not set(scopes) <= allowed_scopes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid token scopes")
    with database(request) as session:
        identity = require_identity(request, session)
        require_csrf(request, identity)
        scope = authorization_scope(request, session, identity.user)
        repository_ids = sorted(set(payload.repository_ids))
        if not set(repository_ids).issubset(scope.repository_ids):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Token references an inaccessible repository",
            )
        space_ids = sorted(set(payload.space_ids) or set(scope.space_ids))
        if not set(space_ids).issubset(scope.space_ids):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Token references an inaccessible knowledge space",
            )
        raw_token = new_secret("cat_")
        expires_at = None
        if payload.expires_in_days:
            expires_at = utc_now() + timedelta(days=payload.expires_in_days)
        token = ApiToken(
            name=payload.name.strip(), token_prefix=raw_token[:12],
            token_hash=digest_secret(raw_token), scopes_json=json.dumps(scopes),
            repository_ids_json=json.dumps(repository_ids), created_by=identity.user.id,
            space_ids_json=json.dumps(space_ids),
            expires_at=expires_at,
        )
        session.add(token)
        session.commit()
        session.refresh(token)
        audit(session, "token.create", "api_token", token.id, identity.user.id)
        session.commit()
        return serialize_token(token) | {"token": raw_token}


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(token_id: str, request: Request):
    with database(request) as session:
        identity = require_identity(request, session)
        require_csrf(request, identity)
        token = session.get(ApiToken, token_id)
        if not token:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
        if token.created_by != identity.user.id and not is_owner_role(identity.user.role):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
        token.revoked_at = utc_now()
        session.add(token)
        audit(session, "token.revoke", "api_token", token.id, identity.user.id)
        session.commit()


def public_user(user: User) -> dict:
    return {
        "id": user.id, "email": user.email, "display_name": user.display_name,
        "role": user.role, "is_active": user.is_active, "created_at": user.created_at,
    }


def serialize_job(job: IndexJob) -> dict:
    return {
        "id": job.id, "repository_id": job.repository_id, "status": job.status,
        "progress": job.progress, "message": job.message, "error": job.error,
        "commit": job.commit, "created_at": job.created_at,
        "started_at": job.started_at, "finished_at": job.finished_at,
    }


def serialize_token(token: ApiToken) -> dict:
    return {
        "id": token.id, "name": token.name, "prefix": token.token_prefix,
        "scopes": json.loads(token.scopes_json),
        "repository_ids": json.loads(token.repository_ids_json),
        "space_ids": json.loads(token.space_ids_json or "[]"),
        "created_at": token.created_at, "expires_at": token.expires_at,
        "revoked_at": token.revoked_at,
    }
