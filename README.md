# CodeAtlas

[English](README.md) | [简体中文](README.zh-CN.md)

CodeAtlas is a private code knowledge base and MCP retrieval service. It indexes
versioned Git repositories, combines vector and MySQL FULLTEXT retrieval (ngram parser),
and returns code evidence with repository, commit, symbol, path, and line metadata.

The public deployment is a controlled evaluation environment backed only by small,
permissively licensed open-source repositories. Private code and existing local
Chroma data are never imported into the public environment.

## Architecture

```mermaid
flowchart LR
    Git["Public or private Git repository"] --> Guard["URL, DNS and branch guard"]
    Guard --> Sync["Shallow cache + immutable worktree"]
    Sync --> Parse["Tree-sitter + bounded text windows"]
    Parse --> Redact["Secret redaction"]
    Redact --> Embed["OpenAI-compatible or hash embedding"]
    Embed --> Chroma["Chroma generation"]
    Redact --> FTS["MySQL FULLTEXT generation"]
    Chroma --> Activate["Atomic active-version switch"]
    FTS --> Activate
    User["Browser session + RBAC"] --> Search["FastAPI search"]
    Token["Hashed MCP token + repository scope"] --> MCP["MCP tools"]
    Activate --> Search
    Activate --> MCP
```

## Components

| Directory | Stack | Responsibility |
|---|---|---|
| `backend` | Python 3.11+, FastAPI, SQLModel, Alembic, Chroma, MCP SDK | Auth, RBAC, Git sync, indexing, retrieval, REST and MCP |
| `frontend` | Vue 3, TypeScript, Vite, Vue Query, Axios, Lucide | Visitor search and administrative console |
| `blog` | Astro Content Collections | Product documentation, architecture notes, RSS and sitemap |
| `deploy` | Nginx, systemd, Bash | Single-server deployment, backup and restore |

## Local Development

Backend:

```powershell
cd D:\agent\CodeAtlas\backend
uv sync --python 3.11 --extra dev
$env:CODEATLAS_DATABASE_URL = "mysql+pymysql://codeatlas:YOUR_PASSWORD@127.0.0.1:3306/codeatlas?charset=utf8mb4"
uv run alembic upgrade head
$env:CODEATLAS_BOOTSTRAP_ADMIN_PASSWORD = "use-a-strong-local-password"
uv run codeatlas create-admin --email admin@example.com --name Administrator
uv run codeatlas seed-demo
uv run uvicorn codeatlas.app:create_app --factory --host 127.0.0.1 --port 8010
```

Local development and tests require MySQL 8.0 with the `ngram` full-text parser.
Create the `codeatlas` database with `utf8mb4_0900_ai_ci`; point
`CODEATLAS_TEST_DATABASE_URL` at a MySQL account allowed to create and drop
temporary `codeatlas_test_*` databases.

Frontend and blog:

```powershell
cd D:\agent\CodeAtlas\frontend
pnpm install
pnpm dev

cd D:\agent\CodeAtlas\blog
pnpm install
pnpm dev
```

Open `http://127.0.0.1:4321/` for the blog and
`http://127.0.0.1:5173/lab/code-kb/` for the application during development.
The production Nginx configuration serves both from one origin.

## Verification

```powershell
cd backend
uv run pytest -q
uv run ruff check .
uv run mypy codeatlas

cd ..\frontend
pnpm lint
pnpm typecheck
pnpm test
pnpm build

cd ..\blog
pnpm build
```

The current backend suite covers authentication, CSRF, RBAC, token hashing,
Git SSRF controls, branch injection, path traversal, secret redaction,
Tree-sitter chunking, RRF, immutable Git worktrees, index activation, rollback,
task recovery, and private repository isolation.

## MCP

Streamable HTTP is exposed at `/mcp` and requires an API Token:

```json
{
  "mcpServers": {
    "codeatlas": {
      "type": "streamable-http",
      "url": "http://codeatlas.example.com:8080/mcp",
      "headers": {
        "Authorization": "Bearer cat_REPLACE_WITH_TOKEN"
      }
    }
  }
}
```

For local stdio, set `CODEATLAS_MCP_TOKEN` and run `codeatlas-mcp`. Available
tools are `list_repositories`, `search_code`, `grep_code`, `get_file`,
`find_references`, and `index_status`.

## Deployment

The current pre-domain deployment binds Uvicorn to `127.0.0.1:8010` and exposes
Nginx on port 80 through an administrator IP allowlist. `systemd` enforces one
worker, a 550 MB soft memory limit and a 700 MB hard limit. Logs stay in
`journald`; they are excluded from backups.

See [deploy/RESTORE.md](deploy/RESTORE.md) and
[docs/operations.md](docs/operations.md) before migrating or restoring data.

To import an existing SQLite deployment after Alembic creates an empty MySQL
schema, run `codeatlas migrate-sqlite --sqlite /path/to/codeatlas.db`. The
command refuses non-empty destinations and verifies every migrated table count.

### GitHub SSH auto-sync

Administrators can add a GitHub repository from the `GitHub 来源` console page.
CodeAtlas generates an Ed25519 Deploy Key on the server and shows only the public
key. Add that key to the repository's GitHub `Settings → Deploy keys` with
read-only access, then save the SSH clone URL such as
`git@github.com:owner/repository.git`. The service checks the configured branch
periodically and queues a normal index job when the commit changes.

Private keys are stored under `CODEATLAS_DATA_DIR/ssh` with restricted file
permissions and are never returned by the API or stored in MySQL. After a
deployment upgrade, run `alembic upgrade head` before creating GitHub sources.

### Embedding model switching

Administrators can add an OpenAI-compatible embedding profile from the
`Embedding 模型` page and click `设为当前`. The page field `credential_ref` is
only a server-side reference such as `embedding-company`; never paste an API
key into the browser. Configure the matching secret in the service environment:

```env
CODEATLAS_CREDENTIAL_EMBEDDING_COMPANY=your-real-key
```

Activating a profile validates the server-side credential and automatically
queues re-index jobs for existing repositories. Both indexing and subsequent
search queries use the active profile's Base URL, model, API key and dimension.
The current Chroma collection dimension must match the profile; changing to a
different dimension requires a separate vector collection migration.

## Security Model

- Public registration is disabled.
- Browser passwords use Argon2id; sessions use HttpOnly cookies plus CSRF.
- API Token plaintext is shown once and only its SHA-256 digest is stored.
- Git hosts are allowlisted and DNS results must be globally routable.
- Embedded credentials, submodules, Git LFS, unsafe branches and oversized
  repositories are rejected.
- File preview rejects path and symlink escape and returns at most 200 lines or
  64 KB.
- Anonymous search is limited to 30 requests per IP per minute.

The Alibaba Cloud security-group rule for the retired Qinglong port `15700`
must still be removed in the cloud console; CodeAtlas does not reuse that port.
