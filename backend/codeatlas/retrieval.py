from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, col, select

from .chunker import read_text
from .embeddings import EmbeddingClient, settings_for_profile
from .models import EmbeddingProfile, Repository, RepositoryAccess, User
from .ranking import fuse_and_rerank, tokenize
from .security import redact_secrets, resolve_repository_file
from .settings import Settings
from .vector_store import VectorStore


class CodeRetriever:
    def __init__(self, settings: Settings, engine):
        self.settings = settings
        self.engine = engine
        self.embedder = EmbeddingClient(settings)
        self.vector_store = VectorStore(settings)

    def _current_embedder(self) -> EmbeddingClient:
        with Session(self.engine) as session:
            profile = session.exec(
                select(EmbeddingProfile).where(EmbeddingProfile.is_active)
            ).first()
        if profile:
            return EmbeddingClient(settings_for_profile(self.settings, profile))
        return self.embedder

    def allowed_repositories(
        self,
        user: User | None,
        scope_repository_ids: tuple[str, ...] | None = None,
    ) -> list[Repository]:
        with Session(self.engine) as session:
            if scope_repository_ids is not None:
                if not scope_repository_ids:
                    return []
                return list(
                    session.exec(
                        select(Repository).where(
                            Repository.status == "ready",
                            col(Repository.id).in_(scope_repository_ids),
                        )
                    )
                )
            if user and user.role == "admin":
                return list(session.exec(select(Repository).where(Repository.status == "ready")))
            if user:
                grants = session.exec(
                    select(RepositoryAccess).where(RepositoryAccess.user_id == user.id)
                ).all()
                granted_ids = {grant.repository_id for grant in grants}
                statement = select(Repository).where(Repository.status == "ready")
                if granted_ids:
                    statement = statement.where(
                        (col(Repository.visibility) == "public")
                        | (col(Repository.id).in_(granted_ids))
                    )
                else:
                    statement = statement.where(Repository.visibility == "public")
                return list(session.exec(statement))
            return list(session.exec(select(Repository).where(
                Repository.status == "ready", Repository.visibility == "public"
            )))

    def search(
        self,
        query: str,
        user: User | None = None,
        repository_ids: list[str] | None = None,
        languages: list[str] | None = None,
        path_prefix: str = "",
        limit: int = 10,
        scope_repository_ids: tuple[str, ...] | None = None,
    ) -> list[dict]:
        query = query.strip()
        if not query or len(query) > 500:
            raise ValueError("query must contain between 1 and 500 characters")
        repositories = self.allowed_repositories(user, scope_repository_ids)
        if repository_ids:
            wanted = set(repository_ids)
            repositories = [repo for repo in repositories if repo.id in wanted]
        generation_ids = [
            repo.active_generation_id
            for repo in repositories
            if repo.active_generation_id
        ]
        if not generation_ids:
            return []
        candidate_limit = 50
        vector = self.vector_store.search(
            self._current_embedder().embed([query])[0], generation_ids, candidate_limit
        )
        lexical = self._lexical_candidates(query, generation_ids, candidate_limit)
        allowed_languages = {value.lower() for value in (languages or [])}

        def matches(candidate: dict) -> bool:
            metadata = candidate["metadata"]
            language = str(metadata.get("language", "")).lower()
            if allowed_languages and language not in allowed_languages:
                return False
            return not path_prefix or str(metadata.get("path", "")).startswith(path_prefix)

        return fuse_and_rerank(
            query,
            [candidate for candidate in vector if matches(candidate)],
            [candidate for candidate in lexical if matches(candidate)],
            max(1, min(limit, 10)),
        )

    def _lexical_candidates(
        self, query: str, generation_ids: list[str], limit: int
    ) -> list[dict]:
        terms = [token for token in tokenize(query) if len(token) > 1][:12]
        if not terms:
            return []
        boolean_query = " ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
        placeholders = ",".join(f":generation_{index}" for index in range(len(generation_ids)))
        statement = text(f"""
            SELECT c.*,
                   MATCH(c.path, c.symbol, c.content)
                   AGAINST (:query IN BOOLEAN MODE) AS lexical_rank
            FROM codechunkrecord c
            WHERE c.generation_id IN ({placeholders})
              AND MATCH(c.path, c.symbol, c.content)
                  AGAINST (:query IN BOOLEAN MODE)
            ORDER BY lexical_rank DESC
            LIMIT :limit
        """)
        parameters: dict[str, str | int] = {
            f"generation_{index}": value
            for index, value in enumerate(generation_ids)
        }
        parameters.update({"query": boolean_query, "limit": limit})
        with self.engine.connect() as connection:
            rows = connection.execute(statement, parameters).mappings().all()
        candidates = []
        for index, row in enumerate(rows, start=1):
            metadata: dict[str, str | int] = {
                "repo": row["repository_id"], "generation_id": row["generation_id"],
                "commit": row["commit"], "path": row["path"], "language": row["language"],
                "symbol": row["symbol"], "start_line": row["start_line"],
                "end_line": row["end_line"],
            }
            candidates.append({
                "id": row["id"],
                "document": row["content"],
                "metadata": metadata,
                "lexical_score": 1.0 / index,
            })
        return candidates

    def get_file(
        self, repository_id: str, relative_path: str, user: User | None,
        start_line: int = 1, end_line: int = 200,
        scope_repository_ids: tuple[str, ...] | None = None,
    ) -> dict:
        repositories = {
            repo.id: repo
            for repo in self.allowed_repositories(user, scope_repository_ids)
        }
        if repository_id not in repositories:
            raise PermissionError("repository is not accessible")
        repository = repositories[repository_id]
        path = resolve_repository_file(Path(repository.local_path), relative_path)
        start = max(1, start_line)
        end = max(start, min(end_line, start + 199))
        lines = redact_secrets(read_text(path)).splitlines()[start - 1:end]
        content = "\n".join(f"{number:>6}: {line}" for number, line in enumerate(lines, start))
        return {
            "repo": repository.id, "commit": repository.last_commit,
            "path": path.relative_to(Path(repository.local_path).resolve()).as_posix(),
            "start_line": start, "end_line": start + max(0, len(lines) - 1),
            "content": content[:65536],
        }

    def grep(
        self, pattern: str, user: User | None, repository_id: str | None = None,
        limit: int = 20, regex: bool = False,
        scope_repository_ids: tuple[str, ...] | None = None,
    ) -> list[dict]:
        if not pattern.strip() or len(pattern) > 200:
            raise ValueError("pattern must contain between 1 and 200 characters")
        repositories = self.allowed_repositories(user, scope_repository_ids)
        if repository_id:
            repositories = [repo for repo in repositories if repo.id == repository_id]
        matches = []
        for repository in repositories:
            command = ["rg", "--json", "--line-number", "--color", "never"]
            if not regex:
                command.append("--fixed-strings")
            command.extend(["--", pattern, repository.local_path])
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=10,
                check=False, encoding="utf-8", errors="replace",
            )
            if completed.returncode not in (0, 1):
                raise RuntimeError(completed.stderr.strip() or "ripgrep failed")
            for line in completed.stdout.splitlines():
                event = json.loads(line)
                if event.get("type") != "match":
                    continue
                data = event["data"]
                path = Path(data["path"]["text"]).resolve()
                try:
                    relative = path.relative_to(Path(repository.local_path).resolve()).as_posix()
                except ValueError:
                    continue
                matches.append({
                    "repo": repository.id, "commit": repository.last_commit,
                    "path": relative, "line": int(data["line_number"]),
                    "text": redact_secrets(data["lines"]["text"].rstrip())[:1000],
                })
                if len(matches) >= min(max(1, limit), 50):
                    return matches
        return matches
