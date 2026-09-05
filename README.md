# CodeAtlas

[English](README.md) | [简体中文](README.zh-CN.md)

CodeAtlas is a knowledge and understanding layer for a team's internal code assets. It
connects GitHub, GitLab, and project documents, provides verifiable code retrieval and
Q&A through permission-aware hybrid RAG, and safely exposes company engineering
conventions and existing implementations to coding agents such as Codex through a
read-only MCP server.

The public deployment is a controlled evaluation environment backed only by small,
permissively licensed open-source repositories. Private code and existing local
Chroma data are never imported into the public environment.

## Current Capabilities

- **Permission-aware knowledge spaces**: repositories, documents, Wiki content,
  browser sessions, and MCP tokens share one authorization boundary enforced before
  retrieval.
- **Verifiable hybrid retrieval**: vector search and MySQL FULLTEXT return repository,
  commit, path, symbol, and line-level evidence.
- **Read-only MCP for Codex**: search code, read bounded file ranges, find references,
  query documents and Wiki pages, and retrieve source-backed engineering conventions.
- **Multi-source ingestion**: GitHub, GitLab, uploaded documents, S3, COS, Notion, and
  Confluence are supported as code or read-only knowledge sources.
- **Protected evaluation delivery**: GitHub Actions verifies builds and tests without
  production credentials; an independently authenticated, operator-approved maintenance
  session performs the server release with backup, health checks, and rollback.

Automatic code Wiki generation, interactive code maps, and guided tours are planned
but are not shipped as current capabilities. See the
[product requirements](docs/product-requirements.zh-CN.md) for the complete roadmap.

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
cd <repo-root>\backend
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
cd <repo-root>\frontend
pnpm install
pnpm dev

cd <repo-root>\blog
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

Streamable HTTP is exposed at `/mcp` and requires a personal read-only token. Inject
the token through an environment variable instead of storing it in the repository or
Codex configuration:

```powershell
$env:CODEATLAS_MCP_TOKEN = Read-Host "CodeAtlas MCP Token"
codex mcp add codeatlas `
  --url "https://codeatlas.example.com/mcp" `
  --bearer-token-env-var CODEATLAS_MCP_TOKEN
```

For local stdio, set `CODEATLAS_MCP_TOKEN` and run `codeatlas-mcp`. Available
tools are `list_repositories`, `search_code`, `grep_code`, `get_file`,
`find_references`, `search_documents`, `search_wiki`, `get_wiki_page`,
`search_knowledge`, `get_company_conventions`, and `index_status`. See
[Codex MCP setup](docs/codex-mcp.zh-CN.md) for environment-only credential
configuration and a project-level `AGENTS.md` template.

## Unified structured RAG

Project documents support Markdown, TXT, CSV, DOCX, XLSX, text PDFs and PPTX.
Chunking is structure-first and semantics-assisted: Word uses heading hierarchy,
paragraphs and tables; Excel uses sheets, headers and row groups; PDF uses pages
and layout text blocks; PowerPoint uses slide titles, bodies, tables and notes;
Wiki uses the Markdown heading tree. Only oversized structural units fall back to
paragraph or sentence boundaries. Embedding distance never decides chunk borders.

Code, document and Wiki chunks use the active Embedding Profile and are written
to Chroma collections isolated by profile and vector dimension. Unified cited
retrieval is available through `/api/v1/knowledge/search` and the MCP
`search_knowledge` tool. Image-only PDF pages are marked `ocr_required` and are
not embedded until an OCR pipeline supplies extracted content.

Embedding profiles support both OpenAI-compatible `/embeddings` and Tencent
TokenHub `/embeddings/multimodal`. For Kinfra use the base URL
`https://tokenhub.tencentmaas.com/v1`, model `kinfra-vl-embedding-2b`, and a
credential reference such as `tencent-kinfra`; keep the full API key only in
the server variable `CODEATLAS_CREDENTIAL_TENCENT_KINFRA`. Probe the returned
dimension before saving; activation validates it again before reindexing.

### External knowledge connectors

The admin-only `External Knowledge Sources` page connects AWS S3, Tencent COS,
Notion and Confluence to an existing document collection. Connectors perform
scheduled scans, skip unchanged revisions, preserve original bytes and provider
provenance, and reuse the same structure-first document parsing, MySQL truth,
Chroma projection and cited RAG path as manual uploads.

External document connectors continue to accept only an opaque `credential_ref`;
they never accept or return cloud secrets. Install the corresponding JSON bundle
in the protected service environment, for example:

```env
CODEATLAS_CREDENTIAL_AWS_DOCS='{"access_key_id":"...","secret_access_key":"..."}'
CODEATLAS_CREDENTIAL_COS_DOCS='{"secret_id":"...","secret_key":"..."}'
CODEATLAS_CREDENTIAL_NOTION_ENGINEERING='{"token":"..."}'
CODEATLAS_CREDENTIAL_CONFLUENCE_ENGINEERING='{"email":"admin@example.com","api_token":"..."}'
```

Object storage supports Bucket/Prefix/Region, paginated inventory, bounded
downloads and ETag/LastModified-based change detection. Notion uses the pinned
official REST API and recursive block traversal; Confluence supports Cloud Basic
Auth and Data Center bearer tokens, Space filtering and storage-format parsing.
Private Confluence hosts must be explicitly authorized with
`CODEATLAS_ALLOWED_EXTERNAL_HOSTS`.

Current scope is scheduled read-only synchronization. Notion/Confluence search
disappearance is not treated as deletion proof, preventing permission changes
from deleting local knowledge. OAuth multi-tenancy, webhooks, attachments and
object-store VersionId/DeleteMarker ingestion remain later enhancements.

## Deployment

The production deployment binds Uvicorn to `127.0.0.1:8010` and exposes the
application through the configured HTTPS domain. Browser sessions, CSRF,
administrator authorization, API tokens and MCP bearer tokens protect privileged
operations; public reachability does not bypass those controls. `systemd` enforces
one worker, a 550 MB soft memory limit and a 700 MB hard limit. Logs stay in
`journald`; they are excluded from backups.

`deploy/install.sh` is HTTPS-only and fails closed. Configure the canonical
HTTPS origin, secure cookies, MCP host and matching certificate before running
it. Port 80 is limited to ACME HTTP-01 plus HTTPS redirects; an optional legacy
port 8080 listener may redirect only and cannot serve or proxy CodeAtlas. Existing
Nginx configurations are validated and the retired IP-allowlist include is
removed rather than silently retained.

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

Administrators can create, edit, test, activate and delete inactive embedding
profiles from the `Embedding 模型` page. The form defaults to SiliconFlow's hosted
`BAAI/bge-m3` (`https://api.siliconflow.cn/v1`, 1024 dimensions). On a verified
HTTPS deployment, an administrator may submit a write-only API key; CodeAtlas
encrypts it with the server-side Fernet key and never returns it. A protected
service-environment credential remains available as a fallback:

```env
CODEATLAS_CREDENTIAL_SILICONFLOW_EMBEDDING=your-real-key
```

Blank API-key fields preserve an existing encrypted key; clearing requires an
explicit action and active profiles cannot lose their only credential. Probe the
dimension and confirm it returns 1024 before activation. Activation validates the
credential, queues re-index jobs for existing repositories, and rebuilds document
and Wiki vectors. Active vector settings cannot be edited in place because that
would mix incompatible vectors. Example environments keep `hash` as a safe
startup fallback when no third-party key is installed.

## Security Model

- Public registration is disabled.
- Browser passwords use Argon2id; sessions use HttpOnly cookies plus CSRF.
- API Token plaintext is shown once and only its SHA-256 digest is stored.
- Git hosts are allowlisted and DNS results must be globally routable.
- Embedded credentials, submodules, Git LFS, unsafe branches and oversized
  repositories are rejected.
- File preview rejects path and symlink escape and returns at most 200 lines or
  64 KB.
- Anonymous search and chat are disabled by default. When explicitly enabled for
  testing, anonymous search remains limited to 30 requests per IP per minute.

The Alibaba Cloud security-group rule for the retired Qinglong port `15700`
must still be removed in the cloud console; CodeAtlas does not reuse that port.
