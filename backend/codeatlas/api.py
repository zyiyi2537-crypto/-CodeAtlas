from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, col, select

from .auth import (
    clear_browser_session,
    create_browser_session,
    require_admin,
    require_csrf,
    require_identity,
    resolve_identity,
)
from .chat import ChatService, ChatUnavailableError
from .documents import chunk_document, extract_text
from .gitlab import GitLabClient, GitLabClientError
from .llm_config import (
    LlmProviderError,
    decrypt_api_key,
    encrypt_api_key,
    new_provider_name,
    normalize_base_url,
    sync_models,
)
from .models import (
    ApiToken,
    AuditEvent,
    CodeChunkRecord,
    Document,
    DocumentChunkRecord,
    DocumentCollection,
    EmbeddingProfile,
    GitLabSource,
    IndexJob,
    LlmProvider,
    Repository,
    RepositoryAccess,
    User,
    UserSession,
    WikiPage,
    utc_now,
)
from .security import (
    digest_secret,
    hash_password,
    mask_credential_ref,
    new_secret,
    validate_credential_ref,
    validate_git_branch,
    validate_public_git_url,
    validate_repository_name,
    verify_password,
)

login_attempts: dict[str, list[float]] = {}
LOGIN_LIMIT = 5
LOGIN_WINDOW = 300


def check_login_rate_limit(identifier: str) -> None:
    now = time.time()
    attempts = login_attempts.get(identifier, [])
    attempts = [t for t in attempts if now - t < LOGIN_WINDOW]
    if len(attempts) >= LOGIN_LIMIT:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts. Please try again later.",
        )
    attempts.append(now)
    login_attempts[identifier] = attempts

router = APIRouter(prefix="/api/v1")


class LoginRequest(BaseModel):
    email: str
    password: str


class RepositoryCreate(BaseModel):
    name: str
    description: str = Field(default="", max_length=500)
    git_url: str
    branch: str = Field(default="main", max_length=200)
    visibility: str = "public"
    license_name: str = Field(default="", max_length=100)
    license_url: str = Field(default="", max_length=1000)


class GitLabSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    group_path: str = Field(min_length=1, max_length=500)
    credential_ref: str = Field(min_length=1, max_length=200)
    poll_interval_seconds: int = Field(default=1800, ge=300, le=86400)


class GitLabProjectImport(BaseModel):
    external_project_id: str = Field(min_length=1, max_length=100)
    visibility: str = "private"


class DocumentCollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class DocumentSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    collection_ids: list[str] = Field(default_factory=list, max_length=20)


class WikiPageCreate(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    sources: list[str] = Field(min_length=1, max_length=50)


class WikiSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class EmbeddingProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    dimension: int = Field(ge=64, le=4096)
    credential_ref: str = Field(min_length=1, max_length=200)
    backend: str = "chroma"


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


class LlmProviderCreate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str = Field(default="", max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    models: list[dict[str, str]] = Field(default_factory=list, max_length=500)


class LlmProviderSyncRequest(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str = Field(default="", max_length=1000)


class MemberCreate(BaseModel):
    email: str
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=200)
    role: str = "member"


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: ["search", "read", "status"])
    repository_ids: list[str] = Field(default_factory=list, max_length=50)
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


def database(request: Request) -> Session:
    return Session(request.app.state.engine)


def serialize_repository(repo: Repository) -> dict:
    return {
        "id": repo.id, "name": repo.name, "description": repo.description,
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


def gitlab_credential(request: Request, credential_ref: str) -> str:
    environment_name = f"CODEATLAS_CREDENTIAL_{credential_ref.upper().replace('-', '_')}"
    value = os.getenv(environment_name, "")
    if not value:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"GitLab credential reference is not configured: {credential_ref}",
        )
    return value


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
    check_login_rate_limit(f"{client_ip}:{payload.email.strip().lower()}")
    with database(request) as session:
        user = session.exec(select(User).where(User.email == payload.email.strip().lower())).first()
        if (
            not user
            or not user.is_active
            or not verify_password(payload.password, user.password_hash)
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
        identity = create_browser_session(session, user, response, request.app.state.settings)
        audit(session, "auth.login", "user", user.id, user.id)
        session.commit()
        return {"user": public_user(user), "csrf_token": identity.session.csrf_token}


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


@router.post("/document-collections", status_code=201)
def create_document_collection(payload: DocumentCollectionCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        collection = DocumentCollection(
            name=payload.name.strip(),
            description=payload.description,
            created_by=identity.user.id,
        )
        session.add(collection)
        session.commit()
        session.refresh(collection)
        return {"id": collection.id, "name": collection.name, "description": collection.description}


@router.get("/document-collections")
def list_document_collections(request: Request):
    with database(request) as session:
        require_identity(request, session)
        statement = select(DocumentCollection).order_by(col(DocumentCollection.created_at).desc())
        return [
            {"id": item.id, "name": item.name, "description": item.description}
            for item in session.exec(statement).all()
        ]


@router.get("/document-collections/{collection_id}/documents")
def list_documents(collection_id: str, request: Request):
    with database(request) as session:
        require_identity(request, session)
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
        collection = session.get(DocumentCollection, collection_id)
        if not collection:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document collection not found")
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read") or not hasattr(upload, "filename"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is required")
        filename = upload.filename or "document"
        content = await upload.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "document is too large")
        try:
            text = extract_text(filename, content)
        except ValueError as exc:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
        title = str(form.get("title") or Path(filename).stem)
        content_type = getattr(upload, "content_type", None)
        document = Document(
            collection_id=collection.id,
            title=title[:300],
            original_filename=filename[:500],
            mime_type=content_type or "application/octet-stream",
            source_path="",
            sha256=__import__("hashlib").sha256(content).hexdigest(),
            created_by=identity.user.id,
        )
        document_path = request.app.state.settings.data_dir / "documents" / document.id
        document_path.mkdir(parents=True, exist_ok=True)
        raw_path = document_path / filename
        raw_path.write_bytes(content)
        document.source_path = str(raw_path)
        chunks = chunk_document(document.title, document.id, collection.id, text)
        session.add(document)
        session.add_all(chunks)
        session.commit()
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
        require_identity(request, session)
        allowed = set(payload.collection_ids)
        statement = select(DocumentChunkRecord)
        if allowed:
            statement = statement.where(col(DocumentChunkRecord.collection_id).in_(allowed))
        rows = session.exec(statement).all()
        terms = [term.lower() for term in payload.query.split() if term.strip()]
        results = []
        for row in rows:
            haystack = f"{row.title} {row.section} {row.content}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                results.append({
                    "source_type": "document",
                    "document_id": row.document_id,
                    "collection_id": row.collection_id,
                    "title": row.title,
                    "section": row.section,
                    "page": row.page,
                    "content": row.content,
                    "score": score,
                })
        return sorted(results, key=lambda item: int(str(item["score"])), reverse=True)[:10]


@router.post("/wiki/pages", status_code=201)
def create_wiki_page(payload: WikiPageCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        page = WikiPage(
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
        if session.exec(select(WikiPage).where(WikiPage.path == page.path)).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Wiki page already exists")
        session.add(page)
        session.commit()
        session.refresh(page)
        return {
            "id": page.id,
            "path": page.path,
            "title": page.title,
            "content": page.content,
            "sources": payload.sources,
            "source_type": "wiki",
        }


@router.post("/wiki/search")
def search_wiki(payload: WikiSearchRequest, request: Request):
    with database(request) as session:
        require_identity(request, session)
        terms = [term.lower() for term in payload.query.split() if term.strip()]
        results = []
        for page in session.exec(select(WikiPage).where(WikiPage.status == "published")).all():
            haystack = f"{page.title} {page.content}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                results.append({
                    "source_type": "wiki",
                    "path": page.path,
                    "title": page.title,
                    "content": page.content,
                    "sources": json.loads(page.sources_json),
                    "score": score,
                })
        return sorted(results, key=lambda item: int(str(item["score"])), reverse=True)[:10]


@router.get("/embedding-profiles")
def list_embedding_profiles(request: Request):
    with database(request) as session:
        require_admin(request, session)
        return [
            {
                "id": item.id, "name": item.name, "base_url": item.base_url,
                "model": item.model, "dimension": item.dimension,
                "credential_ref": mask_credential_ref(item.credential_ref),
                "credential_configured": bool(item.credential_ref), "backend": item.backend,
                "is_active": item.is_active,
            }
            for item in session.exec(select(EmbeddingProfile)).all()
        ]


@router.post("/embedding-profiles", status_code=201)
def create_embedding_profile(payload: EmbeddingProfileCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        if payload.backend != "chroma":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only Chroma is implemented")
        try:
            credential_ref = validate_credential_ref(payload.credential_ref)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        profile = EmbeddingProfile(
            name=payload.name.strip(), base_url=payload.base_url.strip().rstrip("/"),
            model=payload.model.strip(), dimension=payload.dimension,
            credential_ref=credential_ref, backend=payload.backend,
            created_by=identity.user.id,
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return {
            "id": profile.id, "name": profile.name, "base_url": profile.base_url,
            "model": profile.model, "dimension": profile.dimension,
            "credential_ref": mask_credential_ref(profile.credential_ref),
            "credential_configured": bool(profile.credential_ref), "backend": profile.backend,
            "is_active": profile.is_active,
        }


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
        if identity and identity.user.role == "admin":
            repositories = session.exec(
                select(Repository).order_by(col(Repository.created_at).desc())
            ).all()
        else:
            repositories = request.app.state.retriever.allowed_repositories(
                identity.user if identity else None
            )
        return [serialize_repository(repo) for repo in repositories]


@router.post("/repositories", status_code=201)
def create_repository(payload: RepositoryCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
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
def queue_sync(repository_id: str, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        repo = session.get(Repository, repository_id)
        if not repo:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
        active = session.exec(select(IndexJob).where(
            IndexJob.repository_id == repository_id,
            col(IndexJob.status).in_(["queued", "running"]),
        )).first()
        if active:
            raise HTTPException(status.HTTP_409_CONFLICT, "Repository already has an active job")
        job = IndexJob(repository_id=repo.id, created_by=identity.user.id)
        session.add(job)
        audit(session, "repository.sync", "repository", repo.id, identity.user.id)
        session.commit()
        session.refresh(job)
        job_id = job.id
        response = serialize_job(job)
    request.app.state.indexer.submit(job_id)
    return response


@router.post("/search")
def search(payload: SearchRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    with database(request) as session:
        identity = resolve_identity(request, session)
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
def create_llm_provider(payload: LlmProviderCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
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
                request.app.state.settings.data_dir, payload.api_key
            ),
            models_json=json.dumps(payload.models, ensure_ascii=False),
            created_by=identity.user.id,
        )
        session.add(provider)
        session.commit()
        session.refresh(provider)
        return _serialize_llm_provider(provider)


@router.post("/llm/providers/sync")
def sync_llm_provider(payload: LlmProviderSyncRequest, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
    try:
        models = sync_models(payload.base_url, payload.api_key)
    except (LlmProviderError, ValueError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"models": models, "count": len(models)}


@router.post("/llm/providers/{provider_id}/activate")
def activate_llm_provider(provider_id: str, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
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
        session.commit()
        return _serialize_llm_provider(provider)


@router.post("/chat")
def chat(payload: ChatRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    with database(request) as session:
        identity = resolve_identity(request, session)
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
            )
        except ChatUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/repositories/{repository_id}/tree")
def get_tree(
    repository_id: str, request: Request, path: str = Query(default="", max_length=1000),
):
    with database(request) as session:
        identity = resolve_identity(request, session)
        repositories = {
            repo.id: repo
            for repo in request.app.state.retriever.allowed_repositories(
                identity.user if identity else None
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
        repositories = request.app.state.retriever.allowed_repositories(
            identity.user if identity else None
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
        try:
            return request.app.state.retriever.get_file(
                repository_id, path, identity.user if identity else None, start_line, end_line
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
        statement = select(IndexJob)
        if identity.user.role != "admin":
            repository_ids = [
                repository.id
                for repository in request.app.state.retriever.allowed_repositories(
                    identity.user
                )
            ]
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
        require_admin(request, session)
        users = session.exec(select(User).order_by(col(User.created_at))).all()
        return [public_user(user) for user in users]


@router.patch("/members/{user_id}")
def update_member(user_id: str, payload: MemberUpdate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
        if payload.role is not None:
            if payload.role not in {"admin", "member"}:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid role")
            user.role = payload.role
        if payload.is_active is not None:
            user.is_active = payload.is_active
        session.add(user)
        audit(session, "member.update", "user", user.id, identity.user.id)
        session.commit()
        return public_user(user)


@router.delete("/members/{user_id}", status_code=204)
def delete_member(user_id: str, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
        if user.id == identity.user.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete yourself")
        session.delete(user)
        audit(session, "member.delete", "user", user.id, identity.user.id)
        session.commit()


@router.post("/members", status_code=201)
def create_member(payload: MemberCreate, request: Request):
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        email = payload.email.strip().lower()
        if payload.role not in {"admin", "member"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid role")
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
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        if not session.get(User, user_id) or not session.get(Repository, repository_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User or repository not found")
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
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
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
        require_admin(request, session)
        tokens = session.exec(
            select(ApiToken).order_by(col(ApiToken.created_at).desc())
        ).all()
        return [serialize_token(token) for token in tokens]


@router.post("/tokens", status_code=201)
def create_token(payload: TokenCreate, request: Request):
    allowed_scopes = {"search", "read", "status"}
    scopes = sorted(set(payload.scopes))
    if not scopes or not set(scopes) <= allowed_scopes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid token scopes")
    with database(request) as session:
        identity = require_admin(request, session)
        require_csrf(request, identity)
        repository_ids = sorted(set(payload.repository_ids))
        if repository_ids:
            existing_ids = set(
                session.exec(
                    select(Repository.id).where(col(Repository.id).in_(repository_ids))
                ).all()
            )
            if existing_ids != set(repository_ids):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Token references an unknown repository",
                )
        raw_token = new_secret("cat_")
        expires_at = None
        if payload.expires_in_days:
            expires_at = utc_now() + timedelta(days=payload.expires_in_days)
        token = ApiToken(
            name=payload.name.strip(), token_prefix=raw_token[:12],
            token_hash=digest_secret(raw_token), scopes_json=json.dumps(scopes),
            repository_ids_json=json.dumps(repository_ids), created_by=identity.user.id,
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
        identity = require_admin(request, session)
        require_csrf(request, identity)
        token = session.get(ApiToken, token_id)
        if not token:
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
        "created_at": token.created_at, "expires_at": token.expires_at,
        "revoked_at": token.revoked_at,
    }
