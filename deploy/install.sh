#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT=${1:-/root/codeatlas-release}
ENV_FILE=/etc/codeatlas/codeatlas.env
BACKUP_SCRIPT="$SOURCE_ROOT/deploy/backup.sh"
MAINTENANCE_LOCK=/run/lock/codeatlas-maintenance.lock
REVISION_ENV_FILE=/etc/codeatlas/revision.env
RELEASE_MARKER=/opt/codeatlas/RELEASE.json
NGINX_TARGET=/etc/nginx/conf.d/codeatlas.conf
NGINX_DEFAULT=/etc/nginx/conf.d/default.conf
CODEATLAS_SERVICE=/etc/systemd/system/codeatlas.service
BACKEND_TARGET=/opt/codeatlas/backend
BACKEND_CANDIDATE=/opt/codeatlas/backend.next
BACKEND_PREVIOUS=/opt/codeatlas/backend.previous
WEB_TARGET=/var/www/codeatlas
WEB_CANDIDATE=/var/www/codeatlas.next
WEB_PREVIOUS=/var/www/codeatlas.previous
PYTHON_BIN=${CODEATLAS_PYTHON_BIN:-python3.12}
NGINX_CANDIDATE=""
NGINX_PREFLIGHT=""
BACKUP_ARCHIVE=""
RESTORE_STAGE=""
COMPILE_CACHE=""
DB_REVISION_BEFORE=""
DB_TARGET_REVISION=""
MYSQL_DATABASE=""
MIGRATION_STARTED=false
MIGRATION_COMPLETED=false
MUTABLE_STATE_TOUCHED=false
PUBLIC_EXPOSED=false
TRANSACTION_ACTIVE=false

cleanup() {
  if [[ -n "$NGINX_CANDIDATE" ]]; then
    rm -f -- "$NGINX_CANDIDATE"
  fi
  if [[ -n "$NGINX_PREFLIGHT" ]]; then
    rm -f -- "$NGINX_PREFLIGHT"
  fi
  if [[ -n "$RESTORE_STAGE" && -d "$RESTORE_STAGE" ]]; then
    rm -rf -- "$RESTORE_STAGE"
  fi
  if [[ -n "$COMPILE_CACHE" && -d "$COMPILE_CACHE" ]]; then
    rm -rf -- "${COMPILE_CACHE:?}"
  fi
}
trap cleanup EXIT

if [[ $(id -u) -ne 0 ]]; then
  echo "Run this installer as root" >&2
  exit 1
fi
if [[ ! -d "$SOURCE_ROOT/backend" || ! -d "$SOURCE_ROOT/frontend-dist" || \
      ! -d "$SOURCE_ROOT/blog-dist" || ! -f "$SOURCE_ROOT/RELEASE.json" || \
      -L "$SOURCE_ROOT/RELEASE.json" ]]; then
  echo "Release directory is incomplete: $SOURCE_ROOT" >&2
  exit 1
fi
if [[ ! -f "$SOURCE_ROOT/deploy/validate_nginx.py" ]]; then
  echo "Release directory has no Nginx safety validator" >&2
  exit 1
fi
if [[ ! -x "$BACKUP_SCRIPT" ]]; then
  echo "Release directory has no executable backup helper" >&2
  exit 1
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.11 or newer is required: $PYTHON_BIN" >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c \
  'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11 or newer is required: $PYTHON_BIN" >&2
  exit 1
fi
for required_command in nginx rsync git mysql mysqldump sha256sum tar runuser flock; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "Required deployment command is missing: $required_command" >&2
    exit 1
  fi
done

exec 9>"$MAINTENANCE_LOCK"
if ! flock -n 9; then
  echo "Another CodeAtlas maintenance operation is already running" >&2
  exit 1
fi
export CODEATLAS_MAINTENANCE_LOCK_HELD=1

if ! id codeatlas >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/codeatlas --shell /sbin/nologin codeatlas
fi
install -d -o root -g codeatlas -m 0750 /etc/codeatlas
if [[ ! -f "$ENV_FILE" ]]; then
  install -m 0640 -o root -g codeatlas \
    "$SOURCE_ROOT/deploy/env.production.example" "$ENV_FILE"
  echo "Configure $ENV_FILE with the production database and HTTPS domain, then rerun" >&2
  exit 1
fi
chown root:codeatlas "$ENV_FILE"
chmod 0640 "$ENV_FILE"
if ! grep -q '^CODEATLAS_DATABASE_URL=mysql+' "$ENV_FILE" || \
   grep -q '^CODEATLAS_DATABASE_URL=.*change-me' "$ENV_FILE"; then
  echo "Provision MySQL with deploy/provision-mysql.sh before installing CodeAtlas" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

if [[ ${CODEATLAS_PUBLIC_ORIGIN:-} != https://* ]]; then
  echo "CODEATLAS_PUBLIC_ORIGIN must be an HTTPS origin" >&2
  exit 1
fi
if [[ ${CODEATLAS_COOKIE_SECURE:-false} != true ]]; then
  echo "CODEATLAS_COOKIE_SECURE must be true in production" >&2
  exit 1
fi
BUILD_REVISION=${CODEATLAS_BUILD_REVISION:-manual}
if [[ -f "$SOURCE_ROOT/REVISION" ]]; then
  BUILD_REVISION=$(tr -d '\r\n' < "$SOURCE_ROOT/REVISION")
fi
if [[ ! "$BUILD_REVISION" =~ ^[A-Za-z0-9._-]{1,64}$ ]]; then
  echo "Release revision must contain 1-64 safe identifier characters" >&2
  exit 1
fi
"$PYTHON_BIN" -I -S - "$SOURCE_ROOT/RELEASE.json" "$BUILD_REVISION" <<'PY'
import json
import re
import sys
from pathlib import Path

metadata_path = Path(sys.argv[1])
expected_revision = sys.argv[2]
try:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit("Release metadata is not valid JSON") from exc
if metadata["commit"] != expected_revision:
    raise SystemExit("Release metadata commit does not match REVISION")
if re.fullmatch(r"[0-9a-f]{40}", str(metadata.get("tree", ""))) is None:
    raise SystemExit("Release metadata tree is not a Git tree identifier")
PY
CODEATLAS_DOMAIN=${CODEATLAS_PUBLIC_ORIGIN#https://}
if [[ ! "$CODEATLAS_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "CODEATLAS_PUBLIC_ORIGIN must contain only an HTTPS hostname without a path" >&2
  exit 1
fi
MCP_BARE_HOST_PRESENT=false
IFS=',' read -r -a MCP_ALLOWED_HOSTS <<< "${CODEATLAS_MCP_ALLOWED_HOSTS:-}"
trim_edges() {
  local value=$1
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}
for allowed_host in "${MCP_ALLOWED_HOSTS[@]}"; do
  if [[ $(trim_edges "$allowed_host") == "$CODEATLAS_DOMAIN" ]]; then
    MCP_BARE_HOST_PRESENT=true
    break
  fi
done
if [[ "$MCP_BARE_HOST_PRESENT" != true ]]; then
  echo "CODEATLAS_MCP_ALLOWED_HOSTS must include the bare host $CODEATLAS_DOMAIN" >&2
  exit 1
fi
CERT_DIR="/etc/letsencrypt/live/$CODEATLAS_DOMAIN"
if [[ ! -s "$CERT_DIR/fullchain.pem" || ! -s "$CERT_DIR/privkey.pem" ]]; then
  echo "TLS certificate files are missing under $CERT_DIR" >&2
  exit 1
fi

# Build and validate the candidate before copying code or applying migrations.
NGINX_CANDIDATE=$(mktemp /tmp/codeatlas-nginx.XXXXXX.conf)
if [[ -f "$NGINX_TARGET" ]] && grep -Eq 'listen[[:space:]]+443([^0-9]|$)' "$NGINX_TARGET"; then
  "$PYTHON_BIN" - "$NGINX_TARGET" "$NGINX_CANDIDATE" <<'PY'
import re
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:])
content = source.read_text(encoding="utf-8")
content = re.sub(
    r"^[ \t]*include[ \t]+/etc/nginx/snippets/codeatlas-allowlist\.conf;[ \t]*\n?",
    "",
    content,
    flags=re.MULTILINE,
)
if 'add_header Strict-Transport-Security "max-age=31536000" always;' not in content:
    content = content.replace(
        '    add_header X-Frame-Options "DENY" always;\n',
        '    add_header X-Frame-Options "DENY" always;\n'
        '    add_header Strict-Transport-Security "max-age=31536000" always;\n',
    )
content = content.replace(
    '^/(assets|images|lab/code-kb/assets)/.+\\.[a-f0-9]{8,}',
    '^/(_astro|assets|images|lab/code-kb/assets)/.+\\.[a-f0-9_-]{8,}',
)
target.write_text(content, encoding="utf-8")
PY
else
  "$PYTHON_BIN" - \
    "$SOURCE_ROOT/deploy/nginx-codeatlas.conf" "$NGINX_CANDIDATE" "$CODEATLAS_DOMAIN" <<'PY'
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:3])
domain = sys.argv[3]
content = source.read_text(encoding="utf-8").replace("CODEATLAS_DOMAIN", domain)
target.write_text(content, encoding="utf-8")
PY
fi
"$PYTHON_BIN" "$SOURCE_ROOT/deploy/validate_nginx.py" \
  "$NGINX_CANDIDATE" "$CODEATLAS_DOMAIN"

NGINX_PREFLIGHT=$(mktemp /tmp/codeatlas-nginx-preflight.XXXXXX.conf)
cat > "$NGINX_PREFLIGHT" <<EOF
pid /tmp/codeatlas-nginx-preflight.pid;
error_log stderr notice;
events {}
http {
    include /etc/nginx/mime.types;
    include $NGINX_CANDIDATE;
}
EOF
nginx -t -c "$NGINX_PREFLIGHT"

capture_active_state() {
  local unit=$1
  local state
  state=$(systemctl is-active "$unit" 2>/dev/null || true)
  case "$state" in
    active|inactive) printf '%s' "$state" ;;
    *) echo "Cannot determine active state for $unit: ${state:-empty}" >&2; return 1 ;;
  esac
}

capture_load_state() {
  local unit=$1
  local state
  state=$(systemctl show "$unit" --property=LoadState --value 2>/dev/null || true)
  case "$state" in
    loaded|not-found) printf '%s' "$state" ;;
    *) echo "Cannot safely deploy over LoadState for $unit: ${state:-empty}" >&2; return 1 ;;
  esac
}

capture_enabled_state() {
  local unit=$1
  local state
  state=$(systemctl is-enabled "$unit" 2>/dev/null || true)
  case "$state" in
    enabled|disabled) printf '%s' "$state" ;;
    *) echo "Cannot safely restore enabled state for $unit: ${state:-empty}" >&2; return 1 ;;
  esac
}

NGINX_LOAD_STATE=$(capture_load_state nginx) || exit 1
if [[ "$NGINX_LOAD_STATE" != loaded ]]; then
  echo "The nginx unit must be loaded before deployment" >&2
  exit 1
fi
NGINX_ACTIVE_STATE=$(capture_active_state nginx) || exit 1
NGINX_ENABLED_STATE=$(capture_enabled_state nginx) || exit 1
CODEATLAS_LOAD_STATE=$(capture_load_state codeatlas) || exit 1
if [[ "$CODEATLAS_LOAD_STATE" == loaded ]]; then
  CODEATLAS_ACTIVE_STATE=$(capture_active_state codeatlas) || exit 1
  CODEATLAS_ENABLED_STATE=$(capture_enabled_state codeatlas) || exit 1
else
  if [[ -e "$CODEATLAS_SERVICE" || -L "$CODEATLAS_SERVICE" ]]; then
    echo "CodeAtlas unit file exists but systemd reports LoadState=not-found" >&2
    exit 1
  fi
  CODEATLAS_ACTIVE_STATE=inactive
  CODEATLAS_ENABLED_STATE=absent
fi

MYSQL_LOAD_STATE=$(capture_load_state mysqld) || exit 1
MYSQL_ACTIVE_STATE=$(capture_active_state mysqld) || exit 1
[[ "$MYSQL_LOAD_STATE" == loaded && "$MYSQL_ACTIVE_STATE" == active ]] || {
  echo "The existing MySQL service must be loaded and active" >&2
  exit 1
}

install -d -m 0755 /opt/codeatlas /var/www
install -d -m 0755 /opt/codeatlas/blog/src/content
install -d -o codeatlas -g codeatlas -m 0750 /var/lib/codeatlas
DATA_DIR=${CODEATLAS_DATA_DIR:-/var/lib/codeatlas}
BLOG_CONTENT_TARGET=/opt/codeatlas/blog/src/content

rm -rf -- "$BACKEND_CANDIDATE" "$WEB_CANDIDATE"
install -d -m 0755 "$BACKEND_CANDIDATE" "$WEB_CANDIDATE"

rsync -a --delete \
  --exclude '.venv' --exclude 'data' --exclude '.pytest-tmp' \
  "$SOURCE_ROOT/backend/" "$BACKEND_CANDIDATE/"
rsync -a --delete "$SOURCE_ROOT/blog-dist/" "$WEB_CANDIDATE/"
install -d -m 0755 "$WEB_CANDIDATE/lab/code-kb"
rsync -a --delete "$SOURCE_ROOT/frontend-dist/" "$WEB_CANDIDATE/lab/code-kb/"

cmp -s "$BACKEND_TARGET/pyproject.toml" "$BACKEND_CANDIDATE/pyproject.toml" || {
  echo "Backend dependency manifest changed; perform a protected dependency release" >&2
  exit 1
}
cmp -s "$BACKEND_TARGET/uv.lock" "$BACKEND_CANDIDATE/uv.lock" || {
  echo "Backend dependency lock changed; perform a protected dependency release" >&2
  exit 1
}
rsync -a "$BACKEND_TARGET/.venv/" "$BACKEND_CANDIDATE/.venv/"
COMPILE_CACHE=$(mktemp -d /var/tmp/codeatlas-compile.XXXXXXXX)
chmod 0700 "$COMPILE_CACHE"
"$PYTHON_BIN" -I -S -X pycache_prefix="$COMPILE_CACHE" -m compileall -q \
  "$BACKEND_CANDIDATE/codeatlas" "$BACKEND_CANDIDATE/alembic"
rm -rf -- "${COMPILE_CACHE:?}"
COMPILE_CACHE=""
MYSQL_DATABASE=$(PYTHONPATH='' PYTHONHOME='' \
  "$BACKEND_CANDIDATE/.venv/bin/python" - <<'PY'
import os
import re

from sqlalchemy.engine import make_url

database = make_url(os.environ["CODEATLAS_DATABASE_URL"]).database or ""
if re.fullmatch(r"[A-Za-z0-9_]+", database) is None:
    raise SystemExit("CodeAtlas MySQL database name is not safe")
print(database)
PY
)
DB_TARGET_REVISION=$("$PYTHON_BIN" -I -S - "$BACKEND_CANDIDATE/alembic/versions" <<'PY'
import ast
import sys
from pathlib import Path

versions = Path(sys.argv[1])
revisions: dict[str, str | None] = {}
for migration in sorted(versions.glob("*.py")):
    tree = ast.parse(migration.read_text(encoding="utf-8"), filename=str(migration))
    values: dict[str, str | None] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        if not isinstance(target, ast.Name):
            continue
        if target.id not in {"revision", "down_revision"}:
            continue
        if isinstance(value, ast.Constant) and (
            isinstance(value.value, str) or value.value is None
        ):
            values[target.id] = value.value
    revision = values.get("revision")
    if not isinstance(revision, str) or revision in revisions:
        raise SystemExit("Migration revision graph is invalid")
    revisions[revision] = values.get("down_revision")
parents = {parent for parent in revisions.values() if parent is not None}
heads = sorted(set(revisions) - parents)
if len(heads) != 1:
    raise SystemExit("Migration graph must contain exactly one head")
print(heads[0])
PY
)
if [[ ! "$DB_TARGET_REVISION" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "Cannot determine the target database revision" >&2
  exit 1
fi

install -d -m 0700 /var/backups/codeatlas
NGINX_BACKUP_DIR=$(mktemp -d /var/backups/codeatlas/nginx-install-XXXXXXXX)
NGINX_TARGET_EXISTED=false
NGINX_DEFAULT_EXISTED=false
CODEATLAS_SERVICE_EXISTED=false
REVISION_ENV_EXISTED=false
RELEASE_MARKER_EXISTED=false
if [[ -f "$NGINX_TARGET" ]]; then
  install -m 0644 "$NGINX_TARGET" "$NGINX_BACKUP_DIR/codeatlas.conf"
  NGINX_TARGET_EXISTED=true
fi
if [[ -f "$NGINX_DEFAULT" ]]; then
  install -m 0644 "$NGINX_DEFAULT" "$NGINX_BACKUP_DIR/default.conf"
  NGINX_DEFAULT_EXISTED=true
fi
if [[ -f "$CODEATLAS_SERVICE" ]]; then
  install -m 0644 "$CODEATLAS_SERVICE" "$NGINX_BACKUP_DIR/codeatlas.service"
  CODEATLAS_SERVICE_EXISTED=true
fi
if [[ -f "$REVISION_ENV_FILE" ]]; then
  install -m 0640 "$REVISION_ENV_FILE" "$NGINX_BACKUP_DIR/revision.env"
  REVISION_ENV_EXISTED=true
fi
if [[ -e "$RELEASE_MARKER" || -L "$RELEASE_MARKER" ]]; then
  if [[ ! -f "$RELEASE_MARKER" || -L "$RELEASE_MARKER" ]]; then
    echo "Existing release marker is not a regular file" >&2
    exit 1
  fi
  install -m 0644 "$RELEASE_MARKER" "$NGINX_BACKUP_DIR/RELEASE.json"
  RELEASE_MARKER_EXISTED=true
fi

verify_active_state() {
  local unit=$1
  local expected=$2
  local current
  current=$(capture_active_state "$unit") || return 1
  if [[ "$expected" == active ]]; then
    [[ "$current" == active ]]
  else
    [[ "$current" != active ]]
  fi
}

verify_load_state() {
  local unit=$1
  local expected=$2
  [[ $(capture_load_state "$unit") == "$expected" ]]
}

verify_enabled_state() {
  local unit=$1
  local expected=$2
  [[ $(capture_enabled_state "$unit") == "$expected" ]]
}

restore_nginx_files() {
  local failed=0
  if [[ "$NGINX_TARGET_EXISTED" == true ]]; then
    if ! install -m 0644 "$NGINX_BACKUP_DIR/codeatlas.conf" "$NGINX_TARGET"; then
      failed=1
    fi
  else
    if ! rm -f -- "$NGINX_TARGET"; then
      failed=1
    fi
  fi
  if [[ "$NGINX_DEFAULT_EXISTED" == true ]]; then
    if ! install -m 0644 "$NGINX_BACKUP_DIR/default.conf" "$NGINX_DEFAULT"; then
      failed=1
    fi
  else
    if ! rm -f -- "$NGINX_DEFAULT"; then
      failed=1
    fi
  fi
  if [[ "$CODEATLAS_SERVICE_EXISTED" == true ]]; then
    if ! install -m 0644 "$NGINX_BACKUP_DIR/codeatlas.service" "$CODEATLAS_SERVICE"; then
      failed=1
    fi
  else
    if ! rm -f -- "$CODEATLAS_SERVICE"; then
      failed=1
    fi
  fi
  if [[ "$REVISION_ENV_EXISTED" == true ]]; then
    if ! install -m 0640 -o root -g codeatlas \
      "$NGINX_BACKUP_DIR/revision.env" "$REVISION_ENV_FILE"; then
      failed=1
    fi
  else
    if ! rm -f -- "$REVISION_ENV_FILE"; then
      failed=1
    fi
  fi
  if [[ "$RELEASE_MARKER_EXISTED" == true ]]; then
    if ! install -m 0644 "$NGINX_BACKUP_DIR/RELEASE.json" "$RELEASE_MARKER"; then
      failed=1
    fi
  else
    if ! rm -f -- "$RELEASE_MARKER"; then
      failed=1
    fi
  fi
  return "$failed"
}

BACKEND_TARGET_EXISTED=false
WEB_TARGET_EXISTED=false
BACKEND_SWITCHED=false
WEB_SWITCHED=false

switch_release() {
  rm -rf -- "$BACKEND_PREVIOUS" "$WEB_PREVIOUS"
  if [[ -e "$BACKEND_TARGET" || -L "$BACKEND_TARGET" ]]; then
    mv -- "$BACKEND_TARGET" "$BACKEND_PREVIOUS" || return 1
    BACKEND_TARGET_EXISTED=true
  fi
  BACKEND_SWITCHED=true
  mv -- "$BACKEND_CANDIDATE" "$BACKEND_TARGET" || return 1
  if [[ -e "$WEB_TARGET" || -L "$WEB_TARGET" ]]; then
    mv -- "$WEB_TARGET" "$WEB_PREVIOUS" || return 1
    WEB_TARGET_EXISTED=true
  fi
  WEB_SWITCHED=true
  mv -- "$WEB_CANDIDATE" "$WEB_TARGET" || return 1
}

restore_release() {
  local failed=0
  if [[ "$BACKEND_SWITCHED" == true ]]; then
    rm -rf -- "$BACKEND_CANDIDATE.failed"
    if [[ -e "$BACKEND_TARGET" || -L "$BACKEND_TARGET" ]]; then
      if ! mv -- "$BACKEND_TARGET" "$BACKEND_CANDIDATE.failed"; then failed=1; fi
    fi
    if [[ "$BACKEND_TARGET_EXISTED" == true ]]; then
      if ! mv -- "$BACKEND_PREVIOUS" "$BACKEND_TARGET"; then failed=1; fi
    fi
  fi
  if [[ "$WEB_SWITCHED" == true ]]; then
    rm -rf -- "$WEB_CANDIDATE.failed"
    if [[ -e "$WEB_TARGET" || -L "$WEB_TARGET" ]]; then
      if ! mv -- "$WEB_TARGET" "$WEB_CANDIDATE.failed"; then failed=1; fi
    fi
    if [[ "$WEB_TARGET_EXISTED" == true ]]; then
      if ! mv -- "$WEB_PREVIOUS" "$WEB_TARGET"; then failed=1; fi
    fi
  fi
  return "$failed"
}

ensure_restore_stage() {
  if [[ -n "$RESTORE_STAGE" ]]; then
    [[ -d "$RESTORE_STAGE" && ! -L "$RESTORE_STAGE" && \
       -f "$RESTORE_STAGE/codeatlas.sql" && \
       ! -L "$RESTORE_STAGE/codeatlas.sql" ]] || return 1
    return 0
  fi
  [[ -n "$BACKUP_ARCHIVE" && -f "$BACKUP_ARCHIVE" && \
     ! -L "$BACKUP_ARCHIVE" ]] || return 1
  [[ -f "$BACKUP_ARCHIVE.sha256" && \
     ! -L "$BACKUP_ARCHIVE.sha256" ]] || return 1
  sha256sum -c "$BACKUP_ARCHIVE.sha256" >/dev/null || return 1
  local restore_candidate
  restore_candidate=$(mktemp -d /var/backups/codeatlas/restore-XXXXXXXX) \
    || return 1
  chmod 0700 "$restore_candidate" || {
    rm -rf -- "${restore_candidate:?}"
    return 1
  }
  tar -C "$restore_candidate" -xzf "$BACKUP_ARCHIVE" || {
    rm -rf -- "${restore_candidate:?}"
    return 1
  }
  [[ -f "$restore_candidate/codeatlas.sql" && \
     ! -L "$restore_candidate/codeatlas.sql" ]] || {
    rm -rf -- "${restore_candidate:?}"
    return 1
  }
  RESTORE_STAGE=$restore_candidate
  return 0
}

rollback_database() {
  if [[ "$MIGRATION_STARTED" != true ]]; then
    return 0
  fi
  ensure_restore_stage || return 1
  mysql --protocol=socket --user=root <<SQL || return 1
DROP DATABASE IF EXISTS \`$MYSQL_DATABASE\`;
CREATE DATABASE \`$MYSQL_DATABASE\`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
SQL
  mysql --protocol=socket --user=root "$MYSQL_DATABASE" \
    < "$RESTORE_STAGE/codeatlas.sql" || return 1
  local restored_revision
  restored_revision=$(mysql --protocol=socket --user=root \
    --batch --skip-column-names "$MYSQL_DATABASE" \
    -e 'SELECT version_num FROM alembic_version') || return 1
  [[ "$restored_revision" == "$DB_REVISION_BEFORE" ]] || return 1
  printf 'DATABASE_ROLLBACK=PASSED from=%s completed=%s\n' \
    "$DB_TARGET_REVISION" "$MIGRATION_COMPLETED" >&2 || return 1
  return 0
}

restore_mutable_state() {
  if [[ "$MUTABLE_STATE_TOUCHED" != true ]]; then
    return 0
  fi
  ensure_restore_stage || return 1
  local failed=0
  local name
  for name in chroma documents; do
    rm -rf -- "${DATA_DIR:?}/$name" || failed=1
    if [[ -d "$RESTORE_STAGE/$name" && ! -L "$RESTORE_STAGE/$name" ]]; then
      cp -a -- "$RESTORE_STAGE/$name" "$DATA_DIR/$name" || failed=1
      chown -R codeatlas:codeatlas "$DATA_DIR/$name" || failed=1
    fi
  done
  rm -rf -- "$BLOG_CONTENT_TARGET" || failed=1
  if [[ -d "$RESTORE_STAGE/blog/content" && \
        ! -L "$RESTORE_STAGE/blog/content" ]]; then
    install -d -m 0755 "$(dirname "$BLOG_CONTENT_TARGET")" || failed=1
    cp -a -- "$RESTORE_STAGE/blog/content" "$BLOG_CONTENT_TARGET" || failed=1
  fi
  return "$failed"
}

rollback_nginx() {
  local failed=0
  local current_codeatlas_load=""
  systemctl stop nginx >/dev/null 2>&1 || failed=1
  if [[ $(capture_load_state codeatlas 2>/dev/null || true) == loaded ]]; then
    systemctl stop codeatlas >/dev/null 2>&1 || failed=1
  fi
  if ! rollback_database; then
    failed=1
  fi
  if ! restore_mutable_state; then
    failed=1
  fi
  if ! restore_release; then
    failed=1
  fi
  if [[ "$CODEATLAS_ENABLED_STATE" == absent && \
        ( -e "$CODEATLAS_SERVICE" || -L "$CODEATLAS_SERVICE" ) ]]; then
    current_codeatlas_load=$(capture_load_state codeatlas) || current_codeatlas_load=error
    if [[ "$current_codeatlas_load" == loaded ]]; then
      if ! systemctl stop codeatlas; then failed=1; fi
      if ! systemctl disable codeatlas; then failed=1; fi
    elif [[ "$current_codeatlas_load" != not-found ]]; then
      failed=1
    fi
  fi
  if ! restore_nginx_files; then
    failed=1
  fi
  if ! systemctl daemon-reload; then
    failed=1
  fi
  if [[ "$CODEATLAS_ACTIVE_STATE" == active ]]; then
    if ! systemctl restart codeatlas; then failed=1; fi
    local old_backend_healthy=false
    local _attempt
    for _attempt in $(seq 1 30); do
      if curl --fail --silent http://127.0.0.1:8010/api/v1/health >/dev/null; then
        old_backend_healthy=true
        break
      fi
      sleep 1
    done
    if [[ "$old_backend_healthy" != true ]]; then
      failed=1
    fi
  elif [[ "$CODEATLAS_ENABLED_STATE" != absent ]]; then
    if ! systemctl stop codeatlas; then failed=1; fi
  fi
  if [[ "$NGINX_ACTIVE_STATE" == active && $failed -eq 0 ]]; then
    if ! nginx -t || ! systemctl restart nginx; then failed=1; fi
  else
    if ! systemctl stop nginx; then failed=1; fi
  fi
  case "$NGINX_ENABLED_STATE" in
    enabled) if ! systemctl enable nginx; then failed=1; fi ;;
    disabled) if ! systemctl disable nginx; then failed=1; fi ;;
    *) failed=1 ;;
  esac
  case "$CODEATLAS_ENABLED_STATE" in
    enabled) if ! systemctl enable codeatlas; then failed=1; fi ;;
    disabled) if ! systemctl disable codeatlas; then failed=1; fi ;;
    absent) ;;
    *) failed=1 ;;
  esac
  if ! verify_active_state nginx "$NGINX_ACTIVE_STATE"; then failed=1; fi
  if ! verify_load_state nginx loaded; then failed=1; fi
  if ! verify_enabled_state nginx "$NGINX_ENABLED_STATE"; then failed=1; fi
  if [[ "$CODEATLAS_ENABLED_STATE" == absent ]]; then
    if ! verify_load_state codeatlas not-found; then failed=1; fi
  elif ! verify_active_state codeatlas "$CODEATLAS_ACTIVE_STATE" || \
       ! verify_load_state codeatlas loaded || \
       ! verify_enabled_state codeatlas "$CODEATLAS_ENABLED_STATE"; then
    failed=1
  fi
  return "$failed"
}

fail_nginx_switch() {
  local message=$1
  trap - ERR HUP INT TERM
  TRANSACTION_ACTIVE=false
  if [[ "$PUBLIC_EXPOSED" == true ]]; then
    fail_public_switch "$message"
  fi
  if ! rollback_nginx; then
    echo "$message; ROLLBACK FAILED, inspect $NGINX_BACKUP_DIR immediately" >&2
    exit 2
  fi
  echo "$message; previous service and Nginx state restored from $NGINX_BACKUP_DIR" >&2
  exit 1
}

fail_public_switch() {
  local message=$1
  trap - ERR HUP INT TERM
  TRANSACTION_ACTIVE=false
  systemctl stop nginx >/dev/null 2>&1 || true
  echo "$message; public ingress is closed and the new state is retained for review" >&2
  exit 2
}

handle_unexpected_error() {
  local status=$?
  if (( BASH_SUBSHELL > 0 )); then
    exit "$status"
  fi
  trap - ERR HUP INT TERM
  if [[ "$TRANSACTION_ACTIVE" != true ]]; then
    exit "$status"
  fi
  TRANSACTION_ACTIVE=false
  if [[ "$PUBLIC_EXPOSED" == true ]]; then
    systemctl stop nginx >/dev/null 2>&1 || true
    echo "Unexpected release error after public exposure; ingress is closed" >&2
    exit 2
  fi
  if ! rollback_nginx; then
    echo "Unexpected release error; ROLLBACK FAILED, inspect $NGINX_BACKUP_DIR" >&2
    exit 2
  fi
  echo "Unexpected release error; previous state restored" >&2
  exit "$status"
}

handle_signal() {
  local signal=$1
  trap - ERR HUP INT TERM
  if [[ "$TRANSACTION_ACTIVE" != true ]]; then
    exit 128
  fi
  TRANSACTION_ACTIVE=false
  if [[ "$PUBLIC_EXPOSED" == true ]]; then
    systemctl stop nginx >/dev/null 2>&1 || true
    echo "Release interrupted by $signal after public exposure; ingress is closed" >&2
    exit 2
  fi
  if ! rollback_nginx; then
    echo "Release interrupted by $signal; ROLLBACK FAILED, inspect $NGINX_BACKUP_DIR" >&2
    exit 2
  fi
  echo "Release interrupted by $signal; previous state restored" >&2
  exit 1
}

assert_no_active_work() {
  local result
  local index_active
  local external_active
  local extra
  result=$(mysql --protocol=socket --user=root \
    --batch --skip-column-names "$MYSQL_DATABASE" -e \
    "SELECT
       (SELECT COUNT(*) FROM indexjob WHERE status IN ('queued','running')),
       (SELECT COUNT(*) FROM externalsource WHERE sync_status IN ('queued','syncing'));"
  ) || return 1
  [[ "$result" != *$'\n'* ]] || return 1
  IFS=$'\t' read -r index_active external_active extra <<< "$result"
  [[ "$index_active" =~ ^[0-9]+$ && \
     "$external_active" =~ ^[0-9]+$ && -z "$extra" ]] || return 1
  [[ "$result" == "$index_active"$'\t'"$external_active" ]] || return 1
  (( index_active == 0 && external_active == 0 ))
}

trap 'handle_unexpected_error' ERR
trap 'handle_signal HUP' HUP
trap 'handle_signal INT' INT
trap 'handle_signal TERM' TERM
TRANSACTION_ACTIVE=true
systemctl stop nginx || fail_nginx_switch "Nginx stop failed"
systemctl stop codeatlas || fail_nginx_switch "CodeAtlas stop failed"
verify_active_state nginx inactive || fail_nginx_switch "Nginx did not stop cleanly"
verify_active_state codeatlas inactive || fail_nginx_switch "CodeAtlas did not stop cleanly"
assert_no_active_work || fail_nginx_switch \
  "Background indexing or external synchronization is still active"

DB_REVISION_BEFORE=$(
  cd "$BACKEND_TARGET"
  runuser --user codeatlas --preserve-environment -- \
    .venv/bin/python -m alembic -c alembic.ini current | awk 'NR == 1 {print $1}'
) || fail_nginx_switch "Cannot determine the current database revision"
if [[ ! "$DB_REVISION_BEFORE" =~ ^[A-Za-z0-9_]+$ ]]; then
  fail_nginx_switch "Cannot determine the current database revision"
fi
BACKUP_ARCHIVE=$("$BACKUP_SCRIPT") \
  || fail_nginx_switch "Quiesced production backup failed"
if [[ ! "$BACKUP_ARCHIVE" =~ ^/var/backups/codeatlas/codeatlas-[0-9]{8}-[0-9]{6}-[A-Za-z0-9]{8}\.tar\.gz$ || \
      ! -f "$BACKUP_ARCHIVE" || -L "$BACKUP_ARCHIVE" || \
      ! -f "$BACKUP_ARCHIVE.sha256" || -L "$BACKUP_ARCHIVE.sha256" ]]; then
  fail_nginx_switch "Backup helper returned an unsafe archive path"
fi
sha256sum -c "$BACKUP_ARCHIVE.sha256" >/dev/null \
  || fail_nginx_switch "Production backup checksum validation failed"

install -m 0644 "$SOURCE_ROOT/deploy/codeatlas.service" "$CODEATLAS_SERVICE" \
  || fail_nginx_switch "Service unit installation failed"
install -m 0644 "$NGINX_CANDIDATE" "$NGINX_TARGET" \
  || fail_nginx_switch "Nginx candidate installation failed"
rm -f -- "$NGINX_DEFAULT" || fail_nginx_switch "Default Nginx removal failed"
nginx -t || fail_nginx_switch "Nginx candidate validation failed"
switch_release || fail_nginx_switch "Release switch failed"

printf 'CODEATLAS_BUILD_REVISION=%s\n' "$BUILD_REVISION" > "$REVISION_ENV_FILE" \
  || fail_nginx_switch "Revision marker write failed"
chown root:codeatlas "$REVISION_ENV_FILE" \
  || fail_nginx_switch "Revision marker ownership update failed"
chmod 0640 "$REVISION_ENV_FILE" \
  || fail_nginx_switch "Revision marker permission update failed"

MUTABLE_STATE_TOUCHED=true
if [[ -d "$SOURCE_ROOT/blog-content" ]]; then
  rsync -a --delete "$SOURCE_ROOT/blog-content/" "$BLOG_CONTENT_TARGET/" \
    || fail_nginx_switch "Blog source synchronization failed"
fi

MIGRATION_STARTED=true
if ! (
  cd "$BACKEND_TARGET"
  runuser --user codeatlas --preserve-environment -- \
    .venv/bin/python -m alembic -c alembic.ini upgrade head
); then
  fail_nginx_switch "Database migration failed"
fi
DB_REVISION_AFTER=$(
  cd "$BACKEND_TARGET"
  runuser --user codeatlas --preserve-environment -- \
    .venv/bin/python -m alembic -c alembic.ini current | awk 'NR == 1 {print $1}'
) || fail_nginx_switch "Database revision verification failed"
if [[ "$DB_REVISION_AFTER" != "$DB_TARGET_REVISION" ]]; then
  fail_nginx_switch "Database did not reach the target revision"
fi
MIGRATION_COMPLETED=true

install -m 0644 "$SOURCE_ROOT/RELEASE.json" "$RELEASE_MARKER" \
  || fail_nginx_switch "Release metadata installation failed"

systemctl daemon-reload || fail_nginx_switch "systemd daemon reload failed"
systemctl enable nginx codeatlas || fail_nginx_switch "Service enable failed"
systemctl restart codeatlas || fail_nginx_switch "CodeAtlas restart failed"

wait_for_local_health() {
  for _attempt in $(seq 1 30); do
    local response
    response=$(curl --fail --silent http://127.0.0.1:8010/api/v1/health) || {
      sleep 1
      continue
    }
    if BUILD_REVISION="$BUILD_REVISION" "$PYTHON_BIN" -c \
      'import json, os, sys; assert json.load(sys.stdin)["revision"] == os.environ["BUILD_REVISION"]' \
      <<< "$response"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_local_ready() {
  for _attempt in $(seq 1 30); do
    if curl --fail --silent http://127.0.0.1:8010/api/v1/ready >/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_https_health() {
  for _attempt in $(seq 1 30); do
    local response
    response=$(curl --fail --silent \
      --resolve "$CODEATLAS_DOMAIN:443:127.0.0.1" \
      "https://$CODEATLAS_DOMAIN/api/code-kb/health") || {
      sleep 1
      continue
    }
    if BUILD_REVISION="$BUILD_REVISION" "$PYTHON_BIN" -c \
      'import json, os, sys; assert json.load(sys.stdin)["revision"] == os.environ["BUILD_REVISION"]' \
      <<< "$response"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_local_health || fail_nginx_switch "Local CodeAtlas health check failed"
wait_for_local_ready || fail_nginx_switch "Local CodeAtlas readiness check failed"
[[ -f "$WEB_TARGET/index.html" && ! -L "$WEB_TARGET/index.html" ]] \
  || fail_nginx_switch "Blog index is missing"
[[ -f "$WEB_TARGET/lab/code-kb/index.html" && \
   ! -L "$WEB_TARGET/lab/code-kb/index.html" ]] \
  || fail_nginx_switch "Frontend index is missing"
nginx -t || fail_nginx_switch "Nginx validation before exposure failed"

PUBLIC_EXPOSED=true
systemctl start nginx || fail_public_switch "Nginx start failed"
wait_for_https_health || fail_public_switch "HTTPS CodeAtlas health check failed"
curl --fail --silent --resolve "$CODEATLAS_DOMAIN:443:127.0.0.1" \
  "https://$CODEATLAS_DOMAIN/api/code-kb/ready" >/dev/null \
  || fail_public_switch "HTTPS CodeAtlas readiness check failed"
curl --fail --silent --resolve "$CODEATLAS_DOMAIN:443:127.0.0.1" \
  "https://$CODEATLAS_DOMAIN/" >/dev/null \
  || fail_public_switch "Public blog check failed"
curl --fail --silent --resolve "$CODEATLAS_DOMAIN:443:127.0.0.1" \
  "https://$CODEATLAS_DOMAIN/lab/code-kb/" >/dev/null \
  || fail_public_switch "Public frontend check failed"
MCP_STATUS=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --resolve "$CODEATLAS_DOMAIN:443:127.0.0.1" \
  "https://$CODEATLAS_DOMAIN/mcp") \
  || fail_public_switch "Anonymous MCP connectivity check failed"
[[ "$MCP_STATUS" == 401 ]] || fail_public_switch "Anonymous MCP check failed"
TRANSACTION_ACTIVE=false
trap - ERR HUP INT TERM

echo "CodeAtlas is running at https://$CODEATLAS_DOMAIN without an IP allowlist"
echo "Verified rollback backup: $BACKUP_ARCHIVE"
echo "Previous Nginx files are retained under $NGINX_BACKUP_DIR"
