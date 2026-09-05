from __future__ import annotations

import contextvars
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from sqlmodel import Session, select

from .authorization import AuthorizationScope, resolve_token_scope
from .conventions import find_company_conventions
from .database import create_database, initialize_database
from .knowledge_search import KnowledgeSearch
from .models import ApiToken, User
from .retrieval import CodeRetriever
from .security import digest_secret
from .settings import Settings


@dataclass(frozen=True)
class McpIdentity:
    scopes: frozenset[str]
    repository_ids: tuple[str, ...]
    space_ids: tuple[str, ...] = ()
    collection_ids: tuple[str, ...] = ()
    actor_user_id: str | None = None
    space_roles: tuple[tuple[str, str], ...] = ()
    repository_spaces: tuple[tuple[str, str], ...] = ()
    collection_spaces: tuple[tuple[str, str], ...] = ()

    def authorization_scope(self) -> AuthorizationScope:
        return AuthorizationScope(
            actor_user_id=self.actor_user_id,
            space_ids=self.space_ids,
            repository_ids=self.repository_ids,
            collection_ids=self.collection_ids,
            actions=self.scopes,
            space_roles=self.space_roles,
            repository_spaces=self.repository_spaces,
            collection_spaces=self.collection_spaces,
        )


CURRENT_MCP_IDENTITY: contextvars.ContextVar[McpIdentity | None] = contextvars.ContextVar(
    "codeatlas_mcp_identity", default=None
)

READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

OPEN_WORLD_READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TokenAuthMiddleware:
    def __init__(self, app: Any, engine):
        self.app = app
        self.engine = engine

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        authorization = next(
            (value.decode("utf-8") for key, value in scope.get("headers", [])
             if key.lower() == b"authorization"),
            "",
        )
        if not authorization.startswith("Bearer "):
            await self._reject(send)
            return
        raw_token = authorization[7:].strip()
        identity = resolve_token_identity(self.engine, raw_token)
        if identity is None:
            await self._reject(send)
            return
        marker = CURRENT_MCP_IDENTITY.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            CURRENT_MCP_IDENTITY.reset(marker)

    @staticmethod
    async def _reject(send: Any) -> None:
        body = b'{"error":"unauthorized"}'
        await send({
            "type": "http.response.start", "status": 401,
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b"Bearer")],
        })
        await send({"type": "http.response.body", "body": body})


def resolve_token_identity(engine, raw_token: str) -> McpIdentity | None:
    if not raw_token:
        return None
    with Session(engine) as database:
        token = database.exec(
            select(ApiToken).where(ApiToken.token_hash == digest_secret(raw_token))
        ).first()
        if (
            not token
            or token.revoked_at
            or (token.expires_at and token.expires_at <= _utc_now())
        ):
            return None
        owner = database.get(User, token.created_by)
        if not owner or not owner.is_active:
            return None
        token_scope = resolve_token_scope(
            database,
            owner,
            tuple(json.loads(token.repository_ids_json or "[]")),
            tuple(json.loads(token.space_ids_json or "[]")),
        )
        return McpIdentity(
            scopes=frozenset(json.loads(token.scopes_json)),
            repository_ids=token_scope.repository_ids,
            space_ids=token_scope.space_ids,
            collection_ids=token_scope.collection_ids,
            actor_user_id=owner.id,
            space_roles=token_scope.space_roles,
            repository_spaces=token_scope.repository_spaces,
            collection_spaces=token_scope.collection_spaces,
        )


def build_mcp(
    settings: Settings,
    engine,
    retriever: CodeRetriever,
    default_identity: McpIdentity | None = None,
    identity_resolver: Callable[[], McpIdentity | None] | None = None,
    knowledge_search: KnowledgeSearch | None = None,
):
    knowledge_search = knowledge_search or KnowledgeSearch(engine, settings)
    mcp = FastMCP(
        "CodeAtlas",
        instructions=(
            "Before implementing, call get_company_conventions for the task, then find at "
            "least two relevant implementations with search_code or grep_code. Read only "
            "the necessary ranges with get_file and cite repository, commit, path and lines."
        ),
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.mcp_allowed_hosts),
            allowed_origins=[settings.public_origin],
        ),
    )

    def identity(required_scope: str) -> McpIdentity:
        value = CURRENT_MCP_IDENTITY.get()
        if value is None and identity_resolver is not None:
            value = identity_resolver()
        if value is None:
            value = default_identity
        if value is None or required_scope not in value.scopes:
            raise PermissionError(f"MCP token requires the {required_scope} scope")
        return value

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def list_repositories() -> list[dict]:
        """List repositories available to the current MCP token."""
        current = identity("status")
        repositories = retriever.allowed_repositories(
            None, scope_repository_ids=current.repository_ids
        )
        return [{
            "id": repo.id, "name": repo.name, "branch": repo.branch,
            "commit": repo.last_commit, "chunks": repo.chunk_count,
        } for repo in repositories]

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def index_status() -> dict:
        """Return public repository and index status."""
        repositories = list_repositories()
        return {
            "repositories": repositories,
            "vector_chunks": sum(int(repository["chunks"]) for repository in repositories),
        }

    @mcp.tool(annotations=OPEN_WORLD_READ_ONLY_TOOL_ANNOTATIONS)
    def search_code(
        query: str, repository: str | None = None,
        language: str | None = None, top_k: int = 5,
    ) -> list[dict]:
        """Search code using vector and MySQL FULLTEXT retrieval with local reranking."""
        current = identity("search")
        if repository and repository not in current.repository_ids:
            raise PermissionError("repository is outside this token scope")
        repository_ids = [repository] if repository else list(current.repository_ids)
        return retriever.search(
            query, repository_ids=repository_ids or None,
            languages=[language] if language else None, limit=top_k,
            authorization_scope=current.authorization_scope(),
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def grep_code(
        pattern: str, repository: str | None = None,
        limit: int = 20, regex: bool = False,
    ) -> list[dict]:
        """Find bounded exact strings or regular expressions in accessible repositories."""
        current = identity("search")
        if repository and repository not in current.repository_ids:
            raise PermissionError("repository is outside this token scope")
        return retriever.grep(
            pattern,
            None,
            repository,
            limit,
            regex,
            authorization_scope=current.authorization_scope(),
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def find_references(symbol: str, repository: str | None = None, limit: int = 20) -> list[dict]:
        """Find exact textual references to a symbol."""
        return grep_code(symbol, repository, limit, False)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_file(
        repository: str, path: str, start_line: int = 1, end_line: int = 200,
    ) -> dict:
        """Read at most 200 numbered lines from an accessible repository file."""
        current = identity("read")
        if repository not in current.repository_ids:
            raise PermissionError("repository is outside this token scope")
        return retriever.get_file(
            repository,
            path,
            None,
            start_line,
            end_line,
            authorization_scope=current.authorization_scope(),
        )

    @mcp.tool(annotations=OPEN_WORLD_READ_ONLY_TOOL_ANNOTATIONS)
    def search_documents(query: str, collection: str | None = None) -> list[dict]:
        """Search accessible uploaded project documents and return cited sections."""
        current = identity("read")
        if collection and collection not in current.collection_ids:
            raise PermissionError("collection is outside this token scope")
        return knowledge_search.search_documents(
            query,
            [collection] if collection else None,
            current.authorization_scope(),
        )

    @mcp.tool(annotations=OPEN_WORLD_READ_ONLY_TOOL_ANNOTATIONS)
    def search_wiki(query: str) -> list[dict]:
        """Search published source-tracked Wiki pages without loading whole pages."""
        current = identity("read")
        return knowledge_search.search_wiki(query, current.authorization_scope())

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_wiki_page(path: str) -> dict:
        """Read one published Wiki page with its provenance sources."""
        current = identity("read")
        return knowledge_search.get_wiki_page(path, current.authorization_scope())

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_company_conventions(
        language: str = "",
        framework: str = "",
        task: str = "",
        space_id: str | None = None,
    ) -> list[dict]:
        """Return confirmed company conventions with source citations."""
        current = identity("read")
        scope = current.authorization_scope()
        if space_id:
            if space_id not in scope.space_ids:
                raise PermissionError("space is outside this token scope")
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
        with Session(engine) as database:
            return find_company_conventions(
                database,
                scope,
                language=language,
                framework=framework,
                task=task,
            )

    @mcp.tool(annotations=OPEN_WORLD_READ_ONLY_TOOL_ANNOTATIONS)
    def search_knowledge(
        query: str,
        source_types: list[str] | None = None,
        repository: str | None = None,
        collection: str | None = None,
        top_k: int = 10,
    ) -> list[dict]:
        """Search code, structured documents and Wiki pages in one cited result set."""
        current = identity("search")
        wanted = source_types or ["code", "document", "wiki"]
        if any(source_type in {"document", "wiki"} for source_type in wanted):
            if "read" not in current.scopes:
                raise PermissionError("MCP token requires the read scope")
        if repository and repository not in current.repository_ids:
            raise PermissionError("repository is outside this token scope")
        if collection and collection not in current.collection_ids:
            raise PermissionError("collection is outside this token scope")
        return retriever.search_knowledge(
            query,
            repository_ids=[repository] if repository else None,
            collection_ids=[collection] if collection else None,
            source_types=wanted,
            limit=top_k,
            authorization_scope=current.authorization_scope(),
        )

    raw_app = mcp.streamable_http_app()
    return mcp, raw_app, TokenAuthMiddleware(raw_app, engine)


def stdio_main() -> None:
    settings = Settings.load()
    settings.ensure_directories()
    engine = create_database(settings)
    initialize_database(settings, engine)
    raw_token = os.getenv("CODEATLAS_MCP_TOKEN", "").strip()
    identity = resolve_token_identity(engine, raw_token)
    if identity is None:
        raise SystemExit("CODEATLAS_MCP_TOKEN must contain an active API token")
    retriever = CodeRetriever(settings, engine)
    mcp, _raw_app, _http_app = build_mcp(
        settings,
        engine,
        retriever,
        identity_resolver=lambda: resolve_token_identity(engine, raw_token),
    )
    mcp.run(transport="stdio")
