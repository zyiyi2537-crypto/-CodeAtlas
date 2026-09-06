from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import sqlmodel
from cryptography.fernet import Fernet

from codeatlas import database
from codeatlas.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "bash"


def _bash_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if sys.platform == "win32" and len(value) >= 3 and value[1:3] == ":/":
        return f"/{value[0].lower()}{value[2:]}"
    return value


def _run_backup(tmp_path: Path, *, chroma_exists: bool = True):
    data_dir = tmp_path / "custom data"
    app_dir = tmp_path / "custom app"
    backup_dir = tmp_path / "custom backups"
    python_stub = app_dir / "backend" / ".venv" / "bin" / "python"
    python_stub.parent.mkdir(parents=True)
    python_stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "cat >/dev/null\n"
        'if [[ $# == 3 ]]; then\n'
        '  printf "[client]\\n" > "$2"\n'
        '  printf "codeatlas_test\\n" > "$3"\n'
        "else\n"
        '  printf "[]\\n" > "$2"\n'
        "fi\n",
        encoding="utf-8",
        newline="\n",
    )
    python_stub.chmod(0o755)
    if chroma_exists:
        (data_dir / "chroma").mkdir(parents=True)
        (data_dir / "chroma" / "index.bin").write_bytes(b"custom-vector-data")
    (data_dir / "documents").mkdir(parents=True)
    (data_dir / "documents" / "original.txt").write_text("original", encoding="utf-8")
    (data_dir / ".llm-config.key").write_bytes(b"saved-provider-key")
    env_file = tmp_path / "production.env"
    env_file.write_text(
        "\n".join(
            f"{name}={shlex.quote(_bash_path(path))}"
            for name, path in (
                ("CODEATLAS_DATA_DIR", data_dir),
                ("CODEATLAS_APP_DIR", app_dir),
                ("CODEATLAS_BACKUP_DIR", backup_dir),
            )
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    service_log = tmp_path / "service.log"
    probe = """
set -eu
export CODEATLAS_ENV_FILE=$1 SERVICE_LOG=$2 CODEATLAS_MAINTENANCE_LOCK_HELD=1
systemctl() { printf '%s\\n' "$*" >> "$SERVICE_LOG"; }
mysqldump() { printf 'SELECT 1;\\n'; }
"""
    if sys.platform == "win32":
        # NTFS cannot enforce these POSIX modes; Linux CI uses the real commands.
        probe += """
chmod() { :; }
install() {
  if [[ "$1" == -d ]]; then
    mkdir -p -- "${@: -1}"
  else
    cp -- "${@: -2:1}" "${@: -1}"
  fi
}
"""
    probe += 'source "$3"\n'
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("CODEATLAS_")
    }
    result = subprocess.run(
        [BASH, "-c", probe, "backup-test", _bash_path(env_file), _bash_path(service_log),
         _bash_path(ROOT / "deploy" / "backup.sh")],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    return result, backup_dir, service_log


def test_backup_archives_paths_loaded_only_from_the_production_environment(tmp_path):
    result, backup_dir, service_log = _run_backup(tmp_path)

    assert result.returncode == 0, result.stderr
    archives = list(backup_dir.glob("*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0]) as archive:
        assert archive.extractfile("./chroma/index.bin").read() == b"custom-vector-data"
        assert archive.extractfile("./documents/original.txt").read() == b"original"
        assert archive.extractfile("./provider-credentials.key").read() == b"saved-provider-key"
        assert archive.extractfile("./codeatlas.sql").read() == b"SELECT 1;\n"
    assert Path(f"{archives[0]}.sha256").is_file()
    assert service_log.read_text().splitlines() == [
        "is-active --quiet codeatlas", "stop codeatlas", "start codeatlas",
    ]
    assert not list(backup_dir.glob(".stage-*"))


def test_backup_refuses_missing_chroma_before_stopping_the_service(tmp_path):
    result, backup_dir, service_log = _run_backup(tmp_path, chroma_exists=False)

    assert result.returncode != 0
    assert "Required Chroma data directory is missing" in result.stderr
    assert not service_log.exists()
    assert not backup_dir.exists()


def _validate_manifest(monkeypatch, tmp_path, *, documents=(), embedding_keys=(), llm_keys=()):
    session = MagicMock()
    session.__enter__.return_value = session
    session.exec.side_effect = [
        SimpleNamespace(all=lambda rows=rows: rows)
        for rows in ([], documents, embedding_keys, llm_keys)
    ]
    monkeypatch.setattr(sqlmodel, "Session", lambda _engine: session)
    monkeypatch.setattr(database, "create_database", lambda _settings: object())
    monkeypatch.setattr(Settings, "load", lambda: object())
    output = tmp_path / "stage" / "repositories.json"
    monkeypatch.setattr(sys, "argv", [
        "-", str(output), str(tmp_path / "data"), str(tmp_path / "stage"),
    ])
    source = (ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")
    validation = source.rsplit("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    exec(compile(validation, "backup-manifest", "exec"), {"__name__": "__main__"})
    assert output.read_text(encoding="utf-8") == "[]\n"


def test_backup_allows_unused_optional_document_and_credential_storage(monkeypatch, tmp_path):
    (tmp_path / "stage").mkdir()
    _validate_manifest(monkeypatch, tmp_path)


def test_backup_refuses_a_missing_referenced_document(monkeypatch, tmp_path):
    (tmp_path / "stage" / "documents").mkdir(parents=True)
    document = SimpleNamespace(source_path=str(tmp_path / "data" / "documents" / "missing.pdf"))

    with pytest.raises(SystemExit, match="required document source is missing"):
        _validate_manifest(monkeypatch, tmp_path, documents=[document])


@pytest.mark.parametrize("provider", ["embedding_keys", "llm_keys"])
@pytest.mark.parametrize("key_state", ["missing", "wrong"])
def test_backup_refuses_unrestorable_provider_credentials(
    monkeypatch, tmp_path, provider, key_state,
):
    stage = tmp_path / "stage"
    stage.mkdir()
    cipher = Fernet(Fernet.generate_key())
    ciphertext = cipher.encrypt(b"provider-secret").decode("ascii")
    if key_state == "wrong":
        (stage / "provider-credentials.key").write_bytes(Fernet.generate_key())

    with pytest.raises(SystemExit, match="credentials cannot be restored"):
        _validate_manifest(monkeypatch, tmp_path, **{provider: [ciphertext]})


def test_backup_validates_copied_documents_and_provider_keys(monkeypatch, tmp_path):
    stage = tmp_path / "stage"
    (stage / "documents").mkdir(parents=True)
    (stage / "documents" / "original.pdf").write_bytes(b"original-document")
    document = SimpleNamespace(source_path=str(tmp_path / "data" / "documents" / "original.pdf"))
    key = Fernet.generate_key()
    (stage / "provider-credentials.key").write_bytes(key)
    ciphertext = Fernet(key).encrypt(b"provider-secret").decode("ascii")

    _validate_manifest(
        monkeypatch, tmp_path, documents=[document],
        embedding_keys=[ciphertext], llm_keys=[ciphertext],
    )
