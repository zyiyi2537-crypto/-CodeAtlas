#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=${1:-/root/codeatlas-release}
ADMIN_PUBLIC_IP=${2:-}

if [[ $(id -u) -ne 0 ]]; then
  echo "Run this installer as root" >&2
  exit 1
fi
if [[ ! -d "$SOURCE_ROOT/backend" || ! -d "$SOURCE_ROOT/frontend-dist" || ! -d "$SOURCE_ROOT/blog-dist" ]]; then
  echo "Release directory is incomplete: $SOURCE_ROOT" >&2
  exit 1
fi
if [[ ! "$ADMIN_PUBLIC_IP" =~ ^[0-9a-fA-F:.]+$ ]]; then
  echo "Pass the administrator public IP as the second argument" >&2
  exit 1
fi

dnf module enable -y nginx:mainline
dnf install -y --disableexcludes=all --allowerasing nginx rsync git mysql mysql-server

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

if ! id codeatlas >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/codeatlas --shell /sbin/nologin codeatlas
fi

install -d -m 0755 /opt/codeatlas /var/www/codeatlas
install -d -m 0755 /opt/codeatlas/blog/src/content
install -d -o codeatlas -g codeatlas -m 0750 /var/lib/codeatlas
install -d -m 0750 /etc/codeatlas
install -d -m 0755 /etc/nginx/snippets

if [[ ! -f /etc/codeatlas/codeatlas.env ]]; then
  install -m 0640 -o root -g codeatlas \
    "$SOURCE_ROOT/deploy/env.production.example" /etc/codeatlas/codeatlas.env
fi
if ! grep -q '^CODEATLAS_DATABASE_URL=mysql+' /etc/codeatlas/codeatlas.env || \
   grep -q '^CODEATLAS_DATABASE_URL=.*change-me' /etc/codeatlas/codeatlas.env; then
  echo "Provision MySQL with deploy/provision-mysql.sh before installing CodeAtlas" >&2
  exit 1
fi

# Alembic loads settings from the process environment. Keep migrations aligned
# with the production service environment instead of falling back to defaults.
set -a
# shellcheck disable=SC1091
. /etc/codeatlas/codeatlas.env
set +a

# Run database migrations
/opt/codeatlas/backend/.venv/bin/python -m alembic -c /opt/codeatlas/backend/alembic.ini upgrade head

rsync -a --delete \
  --exclude '.venv' --exclude 'data' --exclude '.pytest-tmp' \
  "$SOURCE_ROOT/backend/" /opt/codeatlas/backend/
rsync -a --delete "$SOURCE_ROOT/blog-dist/" /var/www/codeatlas/
if [[ -d "$SOURCE_ROOT/blog-content" ]]; then
  rsync -a --delete "$SOURCE_ROOT/blog-content/" /opt/codeatlas/blog/src/content/
fi
install -d -m 0755 /var/www/codeatlas/lab/code-kb
rsync -a --delete "$SOURCE_ROOT/frontend-dist/" /var/www/codeatlas/lab/code-kb/

python3.12 -m venv /opt/codeatlas/backend/.venv
/opt/codeatlas/backend/.venv/bin/pip install --upgrade pip wheel
/opt/codeatlas/backend/.venv/bin/pip install /opt/codeatlas/backend

install -m 0644 "$SOURCE_ROOT/deploy/codeatlas.service" /etc/systemd/system/codeatlas.service
install -m 0644 "$SOURCE_ROOT/deploy/nginx-codeatlas.conf" /etc/nginx/conf.d/codeatlas.conf
sed "s/ADMIN_PUBLIC_IP/$ADMIN_PUBLIC_IP/g" \
  "$SOURCE_ROOT/deploy/codeatlas-allowlist.conf.example" \
  > /etc/nginx/snippets/codeatlas-allowlist.conf
chmod 0644 /etc/nginx/snippets/codeatlas-allowlist.conf

rm -f /etc/nginx/conf.d/default.conf
nginx -t
systemctl daemon-reload
systemctl enable --now codeatlas
systemctl enable --now nginx

curl --fail --silent http://127.0.0.1/api/code-kb/health >/dev/null
echo "CodeAtlas is running and restricted to $ADMIN_PUBLIC_IP"
