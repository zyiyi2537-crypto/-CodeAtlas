#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=${1:-/root/codeatlas-release}
ENV_FILE=/etc/codeatlas/codeatlas.env
NGINX_TARGET=/etc/nginx/conf.d/codeatlas.conf
NGINX_DEFAULT=/etc/nginx/conf.d/default.conf
CODEATLAS_SERVICE=/etc/systemd/system/codeatlas.service
PYTHON_BIN=${CODEATLAS_PYTHON_BIN:-python3.12}
NGINX_CANDIDATE=""
NGINX_PREFLIGHT=""

cleanup() {
  if [[ -n "$NGINX_CANDIDATE" ]]; then
    rm -f -- "$NGINX_CANDIDATE"
  fi
  if [[ -n "$NGINX_PREFLIGHT" ]]; then
    rm -f -- "$NGINX_PREFLIGHT"
  fi
}
trap cleanup EXIT

if [[ $(id -u) -ne 0 ]]; then
  echo "Run this installer as root" >&2
  exit 1
fi
if [[ ! -d "$SOURCE_ROOT/backend" || ! -d "$SOURCE_ROOT/frontend-dist" || ! -d "$SOURCE_ROOT/blog-dist" ]]; then
  echo "Release directory is incomplete: $SOURCE_ROOT" >&2
  exit 1
fi
if [[ ! -f "$SOURCE_ROOT/deploy/validate_nginx.py" ]]; then
  echo "Release directory has no Nginx safety validator" >&2
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
# shellcheck disable=SC1091
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

dnf module enable -y nginx:mainline
dnf install -y --disableexcludes=all --allowerasing nginx rsync git mysql mysql-server

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

MYSQL_CONFIG_CHANGED=false
if [[ ! -f /etc/my.cnf.d/codeatlas.cnf ]] || \
   ! cmp -s "$SOURCE_ROOT/deploy/mysql-codeatlas.cnf" /etc/my.cnf.d/codeatlas.cnf; then
  install -m 0644 "$SOURCE_ROOT/deploy/mysql-codeatlas.cnf" /etc/my.cnf.d/codeatlas.cnf
  MYSQL_CONFIG_CHANGED=true
fi
systemctl enable --now mysqld
if [[ "$MYSQL_CONFIG_CHANGED" == true ]]; then
  systemctl restart mysqld
fi

install -d -m 0755 /opt/codeatlas /var/www/codeatlas
install -d -m 0755 /opt/codeatlas/blog/src/content
install -d -o codeatlas -g codeatlas -m 0750 /var/lib/codeatlas

rsync -a --delete \
  --exclude '.venv' --exclude 'data' --exclude '.pytest-tmp' \
  "$SOURCE_ROOT/backend/" /opt/codeatlas/backend/
rsync -a --delete "$SOURCE_ROOT/blog-dist/" /var/www/codeatlas/
if [[ -d "$SOURCE_ROOT/blog-content" ]]; then
  rsync -a --delete "$SOURCE_ROOT/blog-content/" /opt/codeatlas/blog/src/content/
fi
install -d -m 0755 /var/www/codeatlas/lab/code-kb
rsync -a --delete "$SOURCE_ROOT/frontend-dist/" /var/www/codeatlas/lab/code-kb/

"$PYTHON_BIN" -m venv /opt/codeatlas/backend/.venv
/opt/codeatlas/backend/.venv/bin/pip install --upgrade pip wheel
/opt/codeatlas/backend/.venv/bin/pip install /opt/codeatlas/backend
(
  cd /opt/codeatlas/backend
  .venv/bin/python -m alembic -c alembic.ini upgrade head
)

install -d -m 0700 /var/backups/codeatlas
NGINX_BACKUP_DIR=$(mktemp -d /var/backups/codeatlas/nginx-install-XXXXXXXX)
NGINX_TARGET_EXISTED=false
NGINX_DEFAULT_EXISTED=false
CODEATLAS_SERVICE_EXISTED=false
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
  return "$failed"
}

rollback_nginx() {
  local failed=0
  local current_codeatlas_load=""
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
  if [[ "$NGINX_ACTIVE_STATE" == active ]]; then
    if ! nginx -t || ! systemctl restart nginx; then failed=1; fi
  else
    if ! systemctl stop nginx; then failed=1; fi
  fi
  if ! verify_active_state nginx "$NGINX_ACTIVE_STATE"; then failed=1; fi
  if [[ "$CODEATLAS_ACTIVE_STATE" == active ]]; then
    if ! systemctl restart codeatlas; then failed=1; fi
  elif [[ "$CODEATLAS_ENABLED_STATE" != absent ]]; then
    if ! systemctl stop codeatlas; then failed=1; fi
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
  if ! rollback_nginx; then
    echo "$message; ROLLBACK FAILED, inspect $NGINX_BACKUP_DIR immediately" >&2
    exit 2
  fi
  echo "$message; previous service and Nginx state restored from $NGINX_BACKUP_DIR" >&2
  exit 1
}

install -m 0644 "$SOURCE_ROOT/deploy/codeatlas.service" "$CODEATLAS_SERVICE" \
  || fail_nginx_switch "Service unit installation failed"
install -m 0644 "$NGINX_CANDIDATE" "$NGINX_TARGET" \
  || fail_nginx_switch "Nginx candidate installation failed"
rm -f -- "$NGINX_DEFAULT" || fail_nginx_switch "Default Nginx removal failed"
nginx -t || fail_nginx_switch "Nginx candidate validation failed"

systemctl daemon-reload || fail_nginx_switch "systemd daemon reload failed"
systemctl enable nginx codeatlas || fail_nginx_switch "Service enable failed"
systemctl restart codeatlas || fail_nginx_switch "CodeAtlas restart failed"
if [[ "$NGINX_ACTIVE_STATE" == active ]]; then
  systemctl reload nginx || fail_nginx_switch "Nginx reload failed"
else
  systemctl start nginx || fail_nginx_switch "Nginx start failed"
fi

wait_for_local_health() {
  for _attempt in $(seq 1 30); do
    if curl --fail --silent http://127.0.0.1:8010/api/v1/health >/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_https_health() {
  for _attempt in $(seq 1 30); do
    if curl --fail --silent --resolve "$CODEATLAS_DOMAIN:443:127.0.0.1" \
      "https://$CODEATLAS_DOMAIN/api/code-kb/health" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_local_health || fail_nginx_switch "Local CodeAtlas health check failed"
wait_for_https_health || fail_nginx_switch "HTTPS CodeAtlas health check failed"

echo "CodeAtlas is running at https://$CODEATLAS_DOMAIN without an IP allowlist"
echo "Previous Nginx files are retained under $NGINX_BACKUP_DIR"
