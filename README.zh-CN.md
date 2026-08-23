# CodeAtlas

[English](README.md) | [简体中文](README.zh-CN.md)

CodeAtlas 是面向企业私有代码的知识库与 MCP 检索服务。它能够索引具有版本信息的 Git 仓库，融合向量检索与 MySQL FULLTEXT 检索（ngram 分词器），并返回包含仓库、提交、符号、路径及行号元数据的代码证据。

公开部署仅作为受控评估环境，只索引少量采用宽松许可证的开源仓库。私有代码和现有本地 Chroma 数据绝不会导入公开环境。

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
cd D:\agent\CodeAtlas\backend
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
cd D:\agent\CodeAtlas\frontend
pnpm install
pnpm dev

cd D:\agent\CodeAtlas\blog
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

Streamable HTTP MCP 暴露于 `/mcp`，并要求提供 API Token：

```json
{
  "mcpServers": {
    "codeatlas": {
      "type": "streamable-http",
      "url": "https://codeatlas.example.com/mcp",
      "headers": {
        "Authorization": "Bearer cat_REPLACE_WITH_TOKEN"
      }
    }
  }
}
```

如需使用本地 stdio，请设置 `CODEATLAS_MCP_TOKEN` 并运行 `codeatlas-mcp`。可用工具包括 `list_repositories`、`search_code`、`grep_code`、`get_file`、`find_references` 和 `index_status`。

> 生产 Token 不应通过公网 HTTP 传输。正式接入应在有效 TLS 证书和 HTTPS 监听器配置完成后，直接使用 `https://` MCP 地址。

## 部署

当前域名前部署方式将 Uvicorn 绑定到 `127.0.0.1:8010`，并由 Nginx 在 80 端口通过管理员 IP 白名单对外提供服务。`systemd` 强制使用单个 worker，软内存限制为 550 MB，硬限制为 700 MB。日志保留在 `journald` 中，不包含在备份内。

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

管理员可以在 `Embedding 模型` 页面添加 OpenAI 兼容的 Embedding 配置，并点击 `设为当前`。页面中的 `credential_ref` 只是服务端凭据引用，例如 `embedding-company`；不要将 API Key 粘贴到浏览器中。请在服务运行环境里配置对应的敏感变量：

```env
CODEATLAS_CREDENTIAL_EMBEDDING_COMPANY=your-real-key
```

激活配置时，系统会验证服务端凭据，并自动为已有仓库创建重新索引任务。索引和后续检索都会使用当前配置的 Base URL、模型、API Key 与向量维度。当前 Chroma Collection 的维度必须与配置一致；切换到不同维度需要执行单独的向量 Collection 迁移。

## 安全模型

- 禁止公开注册。
- 浏览器密码使用 Argon2id；会话使用 HttpOnly Cookie 与 CSRF 防护。
- API Token 明文只显示一次，服务端仅保存其 SHA-256 摘要。
- Git 主机必须位于允许列表中，且 DNS 解析结果必须为可公开路由地址。
- 系统拒绝包含嵌入式凭据的地址、Git Submodule、Git LFS、不安全分支及超大仓库。
- 文件预览会阻止路径与符号链接越界，最多返回 200 行或 64 KB。
- 匿名检索限制为每个 IP 每分钟 30 次请求。

已停用的青龙端口 `15700` 所对应的阿里云安全组规则仍需在云控制台中删除；CodeAtlas 不会复用该端口。
