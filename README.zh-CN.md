# CodeAtlas

[English](README.md) | [简体中文](README.zh-CN.md)

CodeAtlas 是面向研发团队内部代码资产的知识与理解层。它连接 GitHub、GitLab 和项目文档，通过权限感知的混合 RAG 提供可验证的代码检索与问答，并以只读 MCP 服务将公司工程规范和既有实现安全地提供给 Codex 等编码代理。

公开部署仅作为受控评估环境，只索引少量采用宽松许可证的开源仓库。私有代码和现有本地 Chroma 数据绝不会导入公开环境。

## 当前能力

- **权限感知的知识空间**：仓库、文档、Wiki、浏览器会话和 MCP Token 使用统一授权边界，权限在召回前执行。
- **可验证的混合检索**：融合向量检索与 MySQL FULLTEXT，结果包含仓库、Commit、路径、符号和行号。
- **Codex 只读 MCP**：支持搜索代码、读取必要片段、查找引用、检索文档与 Wiki，并获取带源码证据的公司工程规范。
- **多源知识接入**：支持 GitHub、GitLab、手动文档以及 S3、COS、Notion、Confluence 等只读知识源。
- **内部测试部署链路**：GitHub Actions 构建并发布到自有服务器，包含产物校验、健康检查和版本回滚。

自动代码 Wiki、交互式代码地图和引导式导览已进入产品规划，尚未作为当前版本能力发布。完整范围见 [产品需求文档](docs/product-requirements.zh-CN.md)。

## 架构

```mermaid
flowchart LR
    Git["公开或私有 Git 仓库"] --> Guard["URL、DNS 与分支安全检查"]
    Guard --> Sync["浅克隆缓存 + 不可变工作树"]
    Sync --> Parse["Tree-sitter + 有界文本窗口"]
    Parse --> Redact["敏感信息脱敏"]
    Redact --> Embed["OpenAI 兼容或哈希 Embedding"]
    Embed --> Chroma["Chroma 索引代次"]
    Redact --> FTS["MySQL FULLTEXT 索引代次"]
    Chroma --> Activate["原子切换当前版本"]
    FTS --> Activate
    User["浏览器会话 + RBAC"] --> Search["FastAPI 检索"]
    Token["哈希 MCP Token + 仓库权限范围"] --> MCP["MCP 工具"]
    Activate --> Search
    Activate --> MCP
```

## 组件

| 目录 | 技术栈 | 职责 |
|---|---|---|
| `backend` | Python 3.11+、FastAPI、SQLModel、Alembic、Chroma、MCP SDK | 身份认证、RBAC、Git 同步、索引、检索、REST 与 MCP |
| `frontend` | Vue 3、TypeScript、Vite、Vue Query、Axios、Lucide | 访客检索与管理控制台 |
| `blog` | Astro Content Collections | 产品文档、架构说明、RSS 与站点地图 |
| `deploy` | Nginx、systemd、Bash | 单服务器部署、备份与恢复 |

## 本地开发

后端：

```powershell
cd <项目根目录>\backend
uv sync --python 3.11 --extra dev
$env:CODEATLAS_DATABASE_URL = "mysql+pymysql://codeatlas:YOUR_PASSWORD@127.0.0.1:3306/codeatlas?charset=utf8mb4"
uv run alembic upgrade head
$env:CODEATLAS_BOOTSTRAP_ADMIN_PASSWORD = "use-a-strong-local-password"
uv run codeatlas create-admin --email admin@example.com --name Administrator
uv run codeatlas seed-demo
uv run uvicorn codeatlas.app:create_app --factory --host 127.0.0.1 --port 8010
```

本地开发和测试需要 MySQL 8.0，并启用 `ngram` 全文分词器。请使用 `utf8mb4_0900_ai_ci` 排序规则创建 `codeatlas` 数据库；`CODEATLAS_TEST_DATABASE_URL` 应指向一个有权创建和删除临时 `codeatlas_test_*` 数据库的 MySQL 账号。

前端与博客：

```powershell
cd <项目根目录>\frontend
pnpm install
pnpm dev

cd <项目根目录>\blog
pnpm install
pnpm dev
```

开发期间，博客地址为 `http://127.0.0.1:4321/`，应用地址为 `http://127.0.0.1:5173/lab/code-kb/`。生产环境的 Nginx 配置会从同一个源站提供两者。

## 验证

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

当前后端测试覆盖身份认证、CSRF、RBAC、Token 哈希、Git SSRF 防护、分支参数注入、路径穿越、敏感信息脱敏、Tree-sitter 分块、RRF、不可变 Git 工作树、索引激活与回滚、任务恢复以及私有仓库隔离。

## MCP

Streamable HTTP MCP 暴露于 `/mcp`，并要求提供个人只读 Token。Token 通过环境变量注入，不写入仓库或 Codex 配置：

```powershell
$env:CODEATLAS_MCP_TOKEN = Read-Host "CodeAtlas MCP Token"
codex mcp add codeatlas `
  --url "https://codeatlas.example.com/mcp" `
  --bearer-token-env-var CODEATLAS_MCP_TOKEN
```

如需使用本地 stdio，请设置 `CODEATLAS_MCP_TOKEN` 并运行 `codeatlas-mcp`。可用工具包括 `list_repositories`、`search_code`、`grep_code`、`get_file`、`find_references`、`search_documents`、`search_wiki`、`get_wiki_page`、`search_knowledge`、`get_company_conventions` 和 `index_status`。完整的安全配置与项目级 `AGENTS.md` 模板见 [Codex 接入 CodeAtlas MCP](docs/codex-mcp.zh-CN.md)。

## 统一结构化 RAG

项目文档支持 Markdown、TXT、CSV、DOCX、XLSX、文本型 PDF 与 PPTX。切块遵循“结构优先、语义辅助”：Word 按标题层级、段落和表格；Excel 按工作表、表头和行组；PDF 按页和版面文本块；PPT 按幻灯片标题、正文、表格和备注；Wiki 按 Markdown 标题树。只有结构单元超过限制时才在段落或句子边界继续拆分，Embedding 不参与决定切块位置。

代码、文档和 Wiki 使用当前激活的 Embedding Profile 写入按 Profile 与维度隔离的 Chroma Collection，并通过 `/api/v1/knowledge/search` 或 MCP `search_knowledge` 返回统一、可引用的结果。扫描型 PDF 会标记为 `ocr_required`，不会将空页或占位提示写入向量库；需要另行配置 OCR 处理链路后再重建索引。

Embedding Profile 支持 OpenAI-compatible `/embeddings` 和腾讯 TokenHub `/embeddings/multimodal` 两种协议。腾讯 Kinfra 配置建议使用 Base URL `https://tokenhub.tencentmaas.com/v1`、模型 `kinfra-vl-embedding-2b` 和凭据引用 `tencent-kinfra`；完整 API Key 只放入服务器环境变量 `CODEATLAS_CREDENTIAL_TENCENT_KINFRA`。保存前可调用“探测维度”，激活时后端也会再次校验真实返回维度。

> 生产 Token 不应通过公网 HTTP 传输。正式接入应在有效 TLS 证书和 HTTPS 监听器配置完成后，直接使用 `https://` MCP 地址。

### 外部知识源连接器

管理员可在“外部知识源”页面将 AWS S3、腾讯云 COS、Notion 和 Confluence
接入已有文档集。连接器会周期扫描、跳过未变化版本、保留原文件与来源元数据，
并复用手动上传文档的结构优先解析、MySQL 权威数据、Chroma 检索投影和可引用 RAG 链路。

浏览器只接受不透明的 `credential_ref`，不会接收或返回云平台 Secret。真实凭据
必须安装在受保护的服务器环境，例如：

```env
CODEATLAS_CREDENTIAL_AWS_DOCS='{"access_key_id":"...","secret_access_key":"..."}'
CODEATLAS_CREDENTIAL_COS_DOCS='{"secret_id":"...","secret_key":"..."}'
CODEATLAS_CREDENTIAL_NOTION_ENGINEERING='{"token":"..."}'
CODEATLAS_CREDENTIAL_CONFLUENCE_ENGINEERING='{"email":"admin@example.com","api_token":"..."}'
```

对象存储支持 Bucket、Prefix、Region、分页清单、有界下载和基于 ETag/LastModified
的变化检测。Notion 使用固定版本的官方 REST API 并递归读取 Block；Confluence
支持 Cloud Basic Auth 与 Data Center Bearer Token、Space 范围和 Storage Format
解析。企业内网 Confluence 主机必须通过 `CODEATLAS_ALLOWED_EXTERNAL_HOSTS` 显式授权。

当前范围是只读定时同步。Notion/Confluence 页面从搜索结果中消失不会被当作删除证据，
以免权限变化误删本地知识。OAuth 多租户、Webhook、附件以及对象存储
VersionId/DeleteMarker 同步属于后续增强，不计入本阶段完成范围。

## 部署

当前部署方式将 Uvicorn 绑定到 `127.0.0.1:8010`，并由 Nginx 通过 HTTPS 对外提供服务。`systemd` 强制使用单个 worker，软内存限制为 550 MB，硬限制为 700 MB。日志保留在 `journald` 中，不包含在备份内。GitHub Actions 负责检查、构建和向自有服务器发布，不承载 MySQL、Chroma 或应用运行时。

迁移或恢复数据前，请阅读 [deploy/RESTORE.md](deploy/RESTORE.md) 和 [docs/operations.md](docs/operations.md)。

如果需要在 Alembic 创建空 MySQL 数据结构后导入现有 SQLite 部署，请运行：

```text
codeatlas migrate-sqlite --sqlite /path/to/codeatlas.db
```

该命令会拒绝导入到非空目标，并验证每张迁移表的记录数量。

### GitHub SSH 自动同步

管理员可以在控制台的 `GitHub 来源` 页面添加 GitHub 仓库。CodeAtlas 会在服务器生成 Ed25519 Deploy Key，并且只显示公钥。请将该公钥添加到仓库的 GitHub `Settings → Deploy keys`，保持只读权限，然后保存以下格式的 SSH Clone URL：

```text
git@github.com:owner/repository.git
```

服务会定期检查配置的分支，并在提交发生变化时创建标准索引任务。

私钥保存在 `CODEATLAS_DATA_DIR/ssh` 下，具有受限文件权限；API 不会返回私钥，MySQL 也不会存储私钥。部署升级后，请先运行 `alembic upgrade head`，再创建 GitHub 来源。

### Embedding 模型切换

管理员可以在 `Embedding 模型` 页面添加 OpenAI 兼容的 Embedding 配置，并点击 `设为当前`。页面默认预填硅基流动托管的 `BAAI/bge-m3`（Base URL `https://api.siliconflow.cn/v1`，1024 维）。页面中的 `credential_ref` 只是服务端凭据引用；不要将 API Key 粘贴到浏览器中。请在服务运行环境里配置对应的敏感变量：

```env
CODEATLAS_CREDENTIAL_SILICONFLOW_EMBEDDING=your-real-key
```

配置 API Key 后，先点击 `探测维度`，确认返回 1024，再保存并激活。激活配置时，系统会验证服务端凭据，并自动为已有仓库创建重新索引任务，同时重建文档和 Wiki 向量。索引和后续检索都会使用当前配置的 Base URL、模型、API Key 与向量维度。未配置服务器凭据的 Profile 无法激活；示例环境继续使用 `hash` 兜底，避免缺少第三方 Key 时服务无法启动。

## 安全模型

- 禁止公开注册。
- 浏览器密码使用 Argon2id；会话使用 HttpOnly Cookie 与 CSRF 防护。
- API Token 明文只显示一次，服务端仅保存其 SHA-256 摘要。
- Git 主机必须位于允许列表中，且 DNS 解析结果必须为可公开路由地址。
- 系统拒绝包含嵌入式凭据的地址、Git Submodule、Git LFS、不安全分支及超大仓库。
- 文件预览会阻止路径与符号链接越界，最多返回 200 行或 64 KB。
- 匿名检索和匿名问答默认关闭；测试环境显式启用后，匿名检索仍限制为每个 IP 每分钟 30 次请求。

已停用的青龙端口 `15700` 所对应的阿里云安全组规则仍需在云控制台中删除；CodeAtlas 不会复用该端口。
