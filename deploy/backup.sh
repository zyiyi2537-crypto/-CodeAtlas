#!/usr/bin/env bash
set -euo pipefail

DATA_DIR=${CODEATLAS_DATA_DIR:-/var/lib/codeatlas}
APP_DIR=${CODEATLAS_APP_DIR:-/opt/codeatlas}
BACKUP_DIR=${CODEATLAS_BACKUP_DIR:-/var/backups/codeatlas}
STAMP=$(date -u +%Y%m%d-%H%M%S)
STAGE="$BACKUP_DIR/.stage-$STAMP"
ARCHIVE="$BACKUP_DIR/codeatlas-$STAMP.tar.gz"
WAS_ACTIVE=false
MYSQL_CNF=""
MYSQL_DATABASE_FILE=""

install -d -m 0750 "$BACKUP_DIR"
install -d -m 0750 "$STAGE"

cleanup() {
  if [[ "$WAS_ACTIVE" == true ]]; then
    systemctl start codeatlas
  fi
  if [[ -d "$STAGE" ]]; then
    rm -rf -- "$STAGE"
  fi
  [[ -z "$MYSQL_CNF" ]] || rm -f -- "$MYSQL_CNF"
  [[ -z "$MYSQL_DATABASE_FILE" ]] || rm -f -- "$MYSQL_DATABASE_FILE"
}
trap cleanup EXIT

if systemctl is-active --quiet codeatlas; then
  WAS_ACTIVE=true
  systemctl stop codeatlas
fi

set -a
. /etc/codeatlas/codeatlas.env
set +a
MYSQL_CNF=$(mktemp "$BACKUP_DIR/.mysql-client.XXXXXX")
MYSQL_DATABASE_FILE=$(mktemp "$BACKUP_DIR/.mysql-database.XXXXXX")
"$APP_DIR/backend/.venv/bin/python" - "$MYSQL_CNF" "$MYSQL_DATABASE_FILE" <<'PY'
import configparser
import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

config_path, database_path = map(Path, sys.argv[1:])
url = make_url(os.environ["CODEATLAS_DATABASE_URL"])
if url.get_backend_name() != "mysql" or not url.database:
    raise SystemExit("CODEATLAS_DATABASE_URL must identify a MySQL database")
client = {
    "user": url.username or "",
    "password": url.password or "",
    "host": url.host or "127.0.0.1",
    "port": str(url.port or 3306),
    "protocol": "tcp",
}
parser = configparser.RawConfigParser()
parser["client"] = client
with config_path.open("w", encoding="utf-8") as output:
    parser.write(output)
database_path.write_text(url.database + "\n", encoding="utf-8")
PY
chmod 0600 "$MYSQL_CNF" "$MYSQL_DATABASE_FILE"
read -r MYSQL_DATABASE < "$MYSQL_DATABASE_FILE"
mysqldump --defaults-extra-file="$MYSQL_CNF" \
  --single-transaction --routines --triggers --hex-blob \
  --set-gtid-purged=OFF --no-tablespaces --column-statistics=0 \
  "$MYSQL_DATABASE" > "$STAGE/codeatlas.sql"
if [[ -d "$DATA_DIR/chroma" ]]; then
  cp -a "$DATA_DIR/chroma" "$STAGE/chroma"
fi
if [[ -d "$DATA_DIR/documents" ]]; then
  cp -a "$DATA_DIR/documents" "$STAGE/documents"
fi
if [[ -d "$APP_DIR/blog/src/content" ]]; then
  install -d "$STAGE/blog"
  cp -a "$APP_DIR/blog/src/content" "$STAGE/blog/content"
fi
install -m 0640 /etc/codeatlas/codeatlas.env "$STAGE/codeatlas.env"
"$APP_DIR/backend/.venv/bin/python" - "$STAGE/repositories.json" <<'PY'
import json
import sys
from pathlib import Path

from sqlmodel import Session, select

from codeatlas.database import create_database
from codeatlas.models import Repository
from codeatlas.settings import Settings

output_path = sys.argv[1]
columns = (
    "name",
    "description",
    "git_url",
    "branch",
    "visibility",
    "license_name",
    "license_url",
    "last_commit",
)
engine = create_database(Settings.load())
with Session(engine) as session:
    repositories = session.exec(select(Repository).order_by(Repository.name)).all()
payload = [
    {column: getattr(repository, column) for column in columns}
    for repository in repositories
]
Path(output_path).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

tar -C "$STAGE" -czf "$ARCHIVE" .
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
chmod 0640 "$ARCHIVE" "$ARCHIVE.sha256"
echo "$ARCHIVE"
