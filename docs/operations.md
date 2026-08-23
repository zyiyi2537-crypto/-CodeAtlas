# Operations

## Service Checks

```bash
systemctl status codeatlas --no-pager
systemctl status nginx --no-pager
curl --fail http://127.0.0.1:8010/api/v1/health
curl --fail http://127.0.0.1:8010/api/v1/ready
journalctl -u codeatlas -n 100 --no-pager
```

The API must listen only on `127.0.0.1:8010`. Public traffic enters through
Nginx. Port `8888` remains assigned to the Baota panel and port `22` to SSH.

## Bootstrap

Install MySQL 8, apply `deploy/mysql-codeatlas.cnf`, then provision a private
database user. The generated password must be URL-safe because it is embedded
in the SQLAlchemy URL:

```bash
export CODEATLAS_MYSQL_PASSWORD="$(openssl rand -hex 24)"
/root/codeatlas-release/deploy/provision-mysql.sh
unset CODEATLAS_MYSQL_PASSWORD
```

The provisioner binds MySQL to loopback, grants the application account access
only to the `codeatlas` schema, and replaces an empty MySQL root password with
`auth_socket` so only the operating-system root account can administer it.

For an existing SQLite deployment, stop CodeAtlas, run Alembic against the empty
MySQL database, and import the legacy file before restarting:

```bash
systemctl stop codeatlas
set -a; . /etc/codeatlas/codeatlas.env; set +a
sudo -u codeatlas -E /opt/codeatlas/backend/.venv/bin/alembic \
  -c /opt/codeatlas/backend/alembic.ini upgrade head
sudo -u codeatlas -E /opt/codeatlas/backend/.venv/bin/codeatlas \
  migrate-sqlite --sqlite /var/lib/codeatlas/codeatlas.db
systemctl start codeatlas
```

Load production variables, create the first administrator, seed the three demo
repository definitions, and run their first index:

```bash
set -a
. /etc/codeatlas/codeatlas.env
set +a
export CODEATLAS_BOOTSTRAP_ADMIN_PASSWORD='replace-before-running'
sudo -u codeatlas -E /opt/codeatlas/backend/.venv/bin/codeatlas \
  create-admin --email admin@example.com --name Administrator
unset CODEATLAS_BOOTSTRAP_ADMIN_PASSWORD
sudo -u codeatlas -E /opt/codeatlas/backend/.venv/bin/codeatlas seed-demo
sudo -u codeatlas -E /opt/codeatlas/backend/.venv/bin/codeatlas index-demo
```

## Upgrade

Build `frontend/dist` and `blog/dist` outside the server. Upload a complete
release directory, then run `deploy/install.sh`. The installer preserves an
existing `/etc/codeatlas/codeatlas.env`, runs Alembic through `ExecStartPre`,
tests Nginx configuration, and restarts only CodeAtlas and Nginx.

## Backup

Run `deploy/backup.sh` as root. It briefly stops CodeAtlas so MySQL and Chroma
represent the same index state, then creates a SHA-256 protected archive under
`/var/backups/codeatlas` containing:

- a consistent MySQL logical dump;
- Chroma persistent data;
- blog Markdown when present on the server;
- the environment file;
- a JSON repository manifest.

Git caches, worktrees and all logs are excluded because they are reproducible.
Backups are never automatically deleted.

## Pre-domain Access

`/etc/nginx/snippets/codeatlas-allowlist.conf` permits only loopback and the
administrator public IP. Update it and run `nginx -t && systemctl reload nginx`
when the IP changes. Before ICP filing, Nginx also listens on port `8080` because
mainland providers may intercept public HTTP traffic on ports 80 and 443. The
temporary URL is `http://codeatlas.example.com:8080/` and its security-group rule must
remain restricted to the administrator IP. After ICP filing, remove the 8080
listener, bind the domain on ports 80 and 443, add a certificate, set
`CODEATLAS_PUBLIC_ORIGIN` to the HTTPS origin and enable
`CODEATLAS_COOKIE_SECURE=true`.
