from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlmodel import Session

from codeatlas import api
from codeatlas.api import active_login_ips, limiter, login_attempts, login_ip_attempts
from codeatlas.app import create_app
from codeatlas.models import User
from codeatlas.security import hash_password
from codeatlas.settings import Settings


@pytest.fixture
def mysql_database_url() -> str:
    admin_url = make_url(
        os.getenv(
            "CODEATLAS_TEST_DATABASE_URL",
            "mysql+pymysql://root@127.0.0.1:3307/mysql?charset=utf8mb4",
        )
    )
    database_name = f"codeatlas_test_{uuid.uuid4().hex}"
    server_url = admin_url.set(database="mysql")
    admin_engine = create_engine(server_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin_engine.connect() as connection:
        connection.execute(
            text(
                f"CREATE DATABASE `{database_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        )
    try:
        yield admin_url.set(database=database_name).render_as_string(hide_password=False)
    finally:
        with admin_engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


@pytest.fixture
def settings(tmp_path: Path, mysql_database_url: str) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        project_root=tmp_path,
        data_dir=data_dir,
        database_url=mysql_database_url,
        repositories_dir=data_dir / "repositories",
        chroma_path=data_dir / "chroma",
        environment="test",
        public_origin="http://testserver",
        cookie_secure=False,
        allowed_git_hosts=("github.com",),
        mcp_allowed_hosts=("testserver", "127.0.0.1:*", "localhost:*"),
        embedding_mode="hash",
        embedding_base_url="",
        embedding_api_key="",
        embedding_model="hash-embedding-v1",
        embedding_dimension=128,
        llm_base_url="",
        llm_api_key="",
        llm_model="kimi-for-coding",
        max_repository_mb=20,
        max_source_files=1000,
        git_timeout_seconds=30,
    )


@pytest.fixture
def application(settings: Settings):
    limiter.events.clear()
    login_attempts.clear()
    login_ip_attempts.clear()
    active_login_ips.clear()
    api.active_login_verifications = 0
    app = create_app(settings)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(application):
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def admin(application) -> User:
    user = User(
        email="admin@example.com",
        display_name="Administrator",
        password_hash=hash_password("correct horse battery staple"),
        role="admin",
    )
    with Session(application.state.engine) as session:
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def login_admin(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "correct horse battery staple"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    return str(response.json()["csrf_token"])
