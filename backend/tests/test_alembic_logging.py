from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_alembic_environment_preserves_existing_application_loggers() -> None:
    backend = Path(__file__).resolve().parents[1]
    # Isolate fileConfig's global handler changes from pytest's own log capture.
    probe = """
import io
import logging
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from alembic import context
from alembic.config import Config
from codeatlas.settings import Settings

application_logger = logging.getLogger("codeatlas.app")
assert not application_logger.disabled
settings = SimpleNamespace(
    database_url="mysql+pymysql://unused@127.0.0.1/unused",
    ensure_directories=Mock(),
)
with (
    patch.object(context, "config", Config(sys.argv[2]), create=True),
    patch.object(context, "is_offline_mode", return_value=True),
    patch.object(context, "configure") as configure,
    patch.object(context, "begin_transaction"),
    patch.object(context, "run_migrations") as run_migrations,
    patch.object(Settings, "load", return_value=settings),
):
    runpy.run_path(sys.argv[1], run_name="__main__")

configure.assert_called_once()
run_migrations.assert_called_once_with()
assert not application_logger.disabled, "Alembic disabled the application logger"
captured = io.StringIO()
application_logger.addHandler(logging.StreamHandler(captured))
application_logger.error("Polling recovery remains observable")
assert "Polling recovery remains observable" in captured.getvalue()
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(backend / "alembic" / "env.py"),
         str(backend / "alembic.ini")],
        cwd=backend,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
