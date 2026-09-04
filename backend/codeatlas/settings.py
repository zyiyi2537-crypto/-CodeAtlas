from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    database_url: str
    repositories_dir: Path
    chroma_path: Path
    environment: str
    public_origin: str
    cookie_secure: bool
    allow_anonymous_search: bool
    allow_anonymous_chat: bool
    build_revision: str
    allowed_git_hosts: tuple[str, ...]
    mcp_allowed_hosts: tuple[str, ...]
    embedding_mode: str
    embedding_base_url: str
    embedding_api_key: str
    embedding_model: str
    embedding_dimension: int
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    max_repository_mb: int
    max_source_files: int
    git_timeout_seconds: int

    @classmethod
    def load(cls) -> Settings:
        project_root = Path(__file__).resolve().parent.parent
        load_dotenv(project_root / ".env", override=False)
        data_dir = Path(os.getenv("CODEATLAS_DATA_DIR", str(project_root / "data"))).resolve()
        return cls(
            project_root=project_root,
            data_dir=data_dir,
            database_url=os.getenv(
                "CODEATLAS_DATABASE_URL",
                "mysql+pymysql://codeatlas:change-me@127.0.0.1:3306/codeatlas?charset=utf8mb4",
            ).strip(),
            repositories_dir=data_dir / "repositories",
            chroma_path=data_dir / "chroma",
            environment=os.getenv("CODEATLAS_ENV", "development").strip().lower(),
            public_origin=os.getenv(
                "CODEATLAS_PUBLIC_ORIGIN", "http://127.0.0.1:4321"
            ).rstrip("/"),
            cookie_secure=os.getenv("CODEATLAS_COOKIE_SECURE", "false").lower()
            in {"1", "true", "yes", "on"},
            allow_anonymous_search=os.getenv(
                "CODEATLAS_ALLOW_ANONYMOUS_SEARCH", "false"
            ).lower()
            in {"1", "true", "yes", "on"},
            allow_anonymous_chat=os.getenv(
                "CODEATLAS_ALLOW_ANONYMOUS_CHAT", "false"
            ).lower()
            in {"1", "true", "yes", "on"},
            build_revision=os.getenv("CODEATLAS_BUILD_REVISION", "development").strip()[:64],
            allowed_git_hosts=tuple(
                item.strip().lower()
                for item in os.getenv(
                    "CODEATLAS_ALLOWED_GIT_HOSTS", "github.com,gitee.com"
                ).split(",")
                if item.strip()
            ),
            mcp_allowed_hosts=tuple(
                item.strip()
                for item in os.getenv(
                    "CODEATLAS_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*"
                ).split(",")
                if item.strip()
            ),
            embedding_mode=os.getenv("CODEATLAS_EMBEDDING_MODE", "hash").strip().lower(),
            embedding_base_url=os.getenv("CODEATLAS_EMBEDDING_BASE_URL", "").rstrip("/"),
            embedding_api_key=os.getenv("CODEATLAS_EMBEDDING_API_KEY", "").strip(),
            embedding_model=os.getenv("CODEATLAS_EMBEDDING_MODEL", "hash-embedding-v1").strip(),
            embedding_dimension=max(
                64, min(int(os.getenv("CODEATLAS_EMBEDDING_DIMENSION", "1024")), 4096)
            ),
            llm_base_url=os.getenv("CODEATLAS_LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("CODEATLAS_LLM_API_KEY", "").strip(),
            llm_model=os.getenv("CODEATLAS_LLM_MODEL", "kimi-for-coding").strip(),
            max_repository_mb=max(10, int(os.getenv("CODEATLAS_MAX_REPOSITORY_MB", "200"))),
            max_source_files=max(100, int(os.getenv("CODEATLAS_MAX_SOURCE_FILES", "20000"))),
            git_timeout_seconds=max(
                30, min(int(os.getenv("CODEATLAS_GIT_TIMEOUT_SECONDS", "180")), 900)
            ),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.repositories_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_path.mkdir(parents=True, exist_ok=True)
