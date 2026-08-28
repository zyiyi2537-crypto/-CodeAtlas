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
release directory. Before running `deploy/install.sh`, set
`CODEATLAS_PUBLIC_ORIGIN` to the canonical HTTPS origin, set
`CODEATLAS_COOKIE_SECURE=true`, include the domain in
`CODEATLAS_MCP_ALLOWED_HOSTS`, and provision the matching certificate under
`/etc/letsencrypt/live/<domain>/`. A first installation can obtain the
certificate with an ACME standalone challenge before starting the application;
an existing installation should keep its normal webroot renewal flow.

The installer fails before copying or migrating the application when those
HTTPS prerequisites are absent. It preserves an existing production Nginx
configuration only after validating that the canonical domain has a TLS server,
port 80 provides ACME and redirects, and any port 8080 compatibility listener is
redirect-only. It removes the retired IP-allowlist include, validates the
candidate with both `deploy/validate_nginx.py` and `nginx -t`, and restores the
previous Nginx file if native validation fails.

## Backup

Run `deploy/backup.sh` as root. It briefly stops CodeAtlas so MySQL and Chroma
represent the same index state, then creates a SHA-256 protected archive under
`/var/backups/codeatlas` containing:

- a consistent MySQL logical dump;
- Chroma persistent data;
- the provider-credential encryption key when present;
- blog Markdown when present on the server;
- the environment file;
- a JSON repository manifest.

Git caches, worktrees and all logs are excluded because they are reproducible.
Backups are never automatically deleted.

## Domain and HTTPS access

Expose the application through its registered domain on ports 80 and 443. Port
80 serves only ACME HTTP-01 challenges and redirects to the canonical HTTPS
origin. Redirect `www` and any retired IP/port entry point to the same origin.
Set `CODEATLAS_PUBLIC_ORIGIN` to that HTTPS origin and enable
`CODEATLAS_COOKIE_SECURE=true`; include the domain in
`CODEATLAS_MCP_ALLOWED_HOSTS`. The site is publicly reachable, while login,
administrator authorization, CSRF, API-token scopes, MCP bearer tokens and rate
limits continue to protect privileged operations. Keep certificate renewal and
an Nginx reload deploy hook enabled. Do not commit the real server address or
certificate private key to the repository.

After HTTPS and secure cookies are verified, administrators may manage LLM and
Embedding provider API keys through the browser. Keys are write-only, encrypted
with the protected data-directory Fernet key, and never returned by the API or
included in audit detail. Back up the encryption-key file with the environment
and database; losing it makes encrypted provider credentials unrecoverable.
Leaving an edit field blank keeps the existing key. Clearing is an explicit
action, and active configurations cannot be deleted or lose their only key.
