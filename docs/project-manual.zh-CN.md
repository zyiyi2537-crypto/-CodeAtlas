# CodeAtlas 项目说明书

本文面向项目负责人、研发人员和运维人员，说明 CodeAtlas 的产品边界、核心实现、配置方式与交付要求。当前适合从单企业内部试点起步：将已获授权的代码和文档组织成可检索、可引用的知识，再逐步补齐企业身份、源系统权限同步和生产运维能力。

版本日期：2026 年 9 月 6 日。源码位置：`D:\agent\CodeAtlas`。上游仓库：[zyiyi2537-crypto/-CodeAtlas](https://github.com/zyiyi2537-crypto/-CodeAtlas)。本次改进以 `4a3cae9ed46dad759e1f6419bcead9e1c75c8b52` 为基础。本地验证情况见随附验证记录，Linux 验证和线上发布结果以仓库 Actions 记录及服务健康接口返回的版本为准。

## 1 项目定位与能力边界

CodeAtlas 是企业代码资产之上的知识检索与理解层。它连接 Git 仓库、项目文档和外部知识源，通过浏览器与只读 MCP 提供内部实现查询、源码片段阅读、知识问答和公司工程规范。适用场景包括新人理解系统、跨项目复用已有实现、技术排查，以及让编码代理在开发前参考公司规范。

| 能力 | 当前情况 |
| --- | --- |
| 代码检索与浏览 | 已实现授权范围内的向量与全文混合检索、目录浏览、限定行数源码预览 |
| 文档与 Wiki 检索 | 已实现结构化文档接入、已有 Wiki 内容存储与检索、来源引用 |
| 知识问答 | 已实现检索增强问答、个人会话和用户记忆；需要配置可用 LLM |
| 公司工程规范 | 已实现带来源引用的规范管理与查询；编码代理可读取已确认规范 |
| MCP | 已实现 11 个只读工具及个人 Token 的权限约束 |
| 外部知识源 | 已实现 S3、COS、Notion、Confluence 的定时只读同步 |
| 自动代码 Wiki | 自动分析、规划、生成和发布整套仓库 Wiki 仍属规划 |
| 代码地图与导览 | 交互式代码地图、符号级关系图及引导式导览仍属规划 |
| 完整企业身份与租户 | 当前为单 Workspace、本地账号；SSO、SCIM、多租户隔离仍需建设 |

企业内部试点应先选择有限仓库、明确使用人群和数据接收方，并约定检索质量与恢复验收标准。现有功能丰富，但不能直接将原型阶段的权限模型、单机部署和测试覆盖等同于已完成企业生产认证。

## 2 系统架构与目录

浏览器通过同源反向代理访问 FastAPI。后端负责身份、权限、同步、切块、索引和检索；MySQL 保存业务事实与全文索引，Chroma 保存向量投影。LLM 和 Embedding 服务通过配置连接，二者用途与故障处理不同。生产配置使用一个 Uvicorn worker，后台任务运行在进程内。

| 目录或模块 | 主要职责 |
| --- | --- |
| `backend/codeatlas/api.py` | 浏览器 REST 接口、鉴权入口和业务操作 |
| `backend/codeatlas/auth.py` 与 `authorization.py` | 会话身份、空间与仓库访问范围 |
| `backend/codeatlas/indexing.py` 与 `job_queue.py` | 索引代次、任务入队、激活与失败恢复 |
| `backend/codeatlas/retrieval.py` 与 `knowledge_search.py` | 代码、文档、Wiki 检索与结果组织 |
| `backend/codeatlas/connectors.py` 与 `external_sync.py` | 外部源访问、下载限制、增量同步 |
| `backend/codeatlas/mcp_server.py` | 只读 MCP 传输与工具授权 |
| `backend/alembic` | MySQL 数据库迁移 |
| `frontend/src` | Vue 3、TypeScript、Vue Query 控制台 |
| `blog` | Astro 产品说明、文章和静态内容 |
| `deploy` 与 `.github/workflows` | Linux 部署、备份恢复与持续集成 |

### Git 索引流程

1. 管理员选择知识空间、仓库地址和分支，系统检查主机、分支及凭据引用。
2. 任务通过统一队列入库，解析本次 Commit 并准备对应源码快照。公开 GitHub 可使用归档下载，其余路径使用受管 Git 缓存和工作树。
3. 支持的语言通过 Tree-sitter 提取结构，其他支持文件使用受限文本切块；敏感凭据在入库和向量化前脱敏。
4. 同一索引代次写入 MySQL 全文记录和 Chroma 向量，成功后才切换活动代次。
5. 失败任务不替换当前活动版本。后台重启恢复与来源轮询按数据库记录继续调度。

### 查询与问答流程

身份解析先确定可访问空间、仓库和文档集合，用户筛选条件只能缩小范围。检索在该范围内召回并融合结果，返回 Commit、路径、行号或页码等证据。问答再把相关证据和有限上下文发送给所配置的 LLM。对外部模型的代码与文档传输，应纳入企业的数据处理约定。

## 3 账号权限与 MCP

### 账号与授权

本地密码使用 Argon2，长度至少 12 个字符。浏览器会话有效期为 12 小时，Cookie 为 HttpOnly，生产环境须启用 Secure；修改操作校验 CSRF。停用用户会使其浏览器会话及所属 MCP Token 无法继续通过身份检查。

| 层级 | 角色或范围 | 语义 |
| --- | --- | --- |
| 工作区角色 | `owner` | 管理所有工作区角色和全局配置，可管理其他用户的 Token |
| 工作区角色 | `workspace_admin` | 管理成员及资源；不能按 owner 权限提升、管理其他高权限角色 |
| 工作区角色 | `member` | 使用被授权的资源及个人会话、记忆、Token |
| 空间角色 | `viewer`、`editor`、`manager` | 分别扩大到读取检索、生成编辑、空间管理；具体接口仍可能要求全局管理员 |
| 仓库授权 | `public`、`private` 及显式授权 | 私有仓库同时受空间范围和仓库授权约束；public 不代表默认对互联网开放 |

当前用户没有租户成员关系，管理员可管理全部空间，普通用户可读取 `workspace` 可见空间。新增一个 Workspace 数据行并不会建立第二家企业的隔离边界。源系统逐文件权限也不会自动转为本系统 ACL，尤其要审查外部文档集合的受众。

本次本地修复在注销、会话失效与身份刷新时清理浏览器查询和操作缓存，取消旧会话在途请求，并重建页面局部状态。旧身份响应不能覆盖新身份；错误密码仍由登录表单显示。该机制解决同一页面切换账号的缓存残留，不替代服务端权限校验。

初始化使用 `create-admin` 创建 owner。`configure-roles --owner-email ...` 会将其余所有账户改为工作区管理员，只应用于明确需要这种批量角色调整的迁移场景，不作为日常开户步骤。

### MCP 接入

Streamable HTTP 入口为 `/mcp`，需要个人 Bearer Token；本地 stdio 可运行 `codeatlas-mcp`。Token 在数据库中保存摘要，支持到期和撤销，权限还会与持有人当前可访问资源求交集。建议设置到期时间，显式选择空间及仓库。

工具包括 `list_repositories`、`index_status`、`search_code`、`grep_code`、`find_references`、`get_file`、`search_documents`、`search_wiki`、`get_wiki_page`、`search_knowledge` 和 `get_company_conventions`。其中 `find_references` 是文本引用搜索，不是完整语义调用图。文档和 Wiki 读取需要 `read`，统一检索还需要 `search`。

Token 的仓库列表为空时，只允许当前授权范围内的公开仓库；文档集合仍依据选定知识空间计算。Token 创建时若未指定空间，系统使用创建者当时可访问的空间。不能把“没有选择仓库”理解为“完全没有数据权限”。

编码代理的推荐顺序是：先查已确认工程规范，再找相关实现，最后按引用的 Commit 和行号读取必要片段。HTTP 客户端应从环境变量 `CODEATLAS_MCP_TOKEN` 注入 Token，不将明文写入源码、共享配置或说明书。具体客户端配置见仓库中的 `docs/codex-mcp.zh-CN.md`。

## 4 Git 与文档接入

### Git 仓库

管理员配置空间、仓库名、分支和可见性后触发首次索引。HTTPS 地址只接受允许的 Git 主机并检查公共地址；SSH 仓库也受主机白名单约束。私有 GitHub 可使用服务器生成的 Ed25519 Deploy Key，向 GitHub 配置只读公钥，私钥保留在服务端。GitLab 使用服务端凭据引用并通过来源配置发现项目。

当前默认仓库上限为 200 MB，源文件数上限为 20,000，Git 超时默认 180 秒；实际策略以环境变量和接入路径为准。Git 缓存与工作树由系统管理，不应手工编辑。支持语言的深度结构分析重点是 Java、Python、JavaScript 和 TypeScript，其他支持扩展名不代表同等语义精度。

### 项目文档

| 类型 | 主要结构与引用信息 |
| --- | --- |
| Markdown、TXT、CSV | 标题、文本段落或行组 |
| DOCX | 标题层级、段落与表格 |
| XLSX | 工作表、表头、行组和行范围 |
| PDF | 页码与文本块；纯图像页标为 `ocr_required` |
| PPTX | 幻灯片标题、正文、表格与备注 |

先创建目标文档集合，再上传文件或连接外部源。切块优先保留结构，超长单元再按段落或句子拆分。图像 PDF 不会自动获得完整 OCR 内容；扫描件应先进入独立 OCR 处理流程。原始文档保存在数据目录，数据库与向量是其派生检索状态的一部分。

### 外部知识源

S3/COS 按 Bucket、Prefix、Region 等配置分页扫描，利用版本标识跳过未变内容。Notion 和 Confluence 通过只读连接获取内容，保留来源 URL、外部 ID 和修订信息。外部源最多 20,000 项，单文件最多 20 MB，一次同步下载总量最多 100 MB。

外部连接器只接收 `credential_ref`，实际密钥由受保护的服务环境提供。引用 `notion-engineering` 对应变量 `CODEATLAS_CREDENTIAL_NOTION_ENGINEERING`；不同连接器要求不同 JSON 凭据字段。私有 Confluence 主机必须显式配置 `CODEATLAS_ALLOWED_EXTERNAL_HOSTS`。

Notion/Confluence 的搜索结果消失不作为删除证明，避免把临时权限变化误当成永久删除。因此，源权限撤销后的内容清理仍需明确运营流程。OAuth 多租户、Webhook、附件全量接入及源文档 ACL 同步尚未完备。导入前必须确认目标集合成员均有权阅读该源内容。

## 5 检索降级与可信引用

代码检索结合向量召回和 MySQL FULLTEXT，再通过 RRF 融合、路径与符号信号调整以及重叠片段抑制返回结果。代码、文档和 Wiki 的分数不可简单视为相同尺度，统一检索会按各来源排名组织结果。

本次本地修复在查询向量服务抛出 `EmbeddingUnavailableError` 时保留授权范围内的词法检索，返回的每条结果带 `degraded=true` 和 `degradation_reason=embedding_unavailable`。目前前端没有专门的降级提示，空结果 `[]` 也不携带该状态，应结合接口结果及后端日志判断。这是 Embedding 查询故障的有限降级，不是所有数据库、Chroma 或模型故障的通用恢复；永久传输配置异常和索引写入失败不会伪装成成功。

文件预览现可携带引用的 `commit`。当仓库活动 Commit 已变化时，HTTP 接口返回 409，MCP 返回对应版本不可用错误，前端提示重新检索并禁止复制错误版本内容。当前没有保证长期保留任意历史 Commit 的在线预览；不传 Commit 的客户端仍读取当前活动版本。

| 现象 | 含义与处理 |
| --- | --- |
| 无检索结果 | 检查权限、活动索引、查询范围及源文件是否被排除 |
| 词法降级结果 | 仍可使用关键词证据；检查 Embedding 服务、凭据及连接状态 |
| 源码预览 409 | 引用版本已不再活动；重新检索并使用新引用 |
| 登录或访问 401 | 会话或 Token 无效、过期或被撤销；重新建立身份 |
| 修改操作 403 | 检查角色、空间权限及 CSRF；不能只通过修改页面解除 |
| 文档 `ocr_required` | 缺少可提取文本，完成 OCR 后再进入索引 |

本次统一了凭据识别与脱敏规则，覆盖 GitHub、GitLab、AWS、OpenAI、Slack、JWT、Google、Azure 等已有格式及自然语言密钥声明，保留字段结构和换行。规则脱敏不能保证识别所有秘密，也不会替代原始文件访问控制、密钥轮换或企业 DLP。已入库的历史内容需安排重新索引才能应用新规则。

## 6 关键配置

后端从 `backend/.env` 与进程环境加载设置，已存在的进程环境变量优先。开发配置与生产配置分开维护，真实密码和 Token 不进入 Git。默认 hash embedding 可用于连通性验证，但不能代表真实语义检索质量。

| 配置 | 用途与建议 |
| --- | --- |
| `CODEATLAS_DATABASE_URL` | MySQL 连接；生产应用账号只授权业务数据库 |
| `CODEATLAS_DATA_DIR` | 原始文档、Chroma、受管仓库和加密密钥所在目录 |
| `CODEATLAS_PUBLIC_ORIGIN` | 实际浏览器来源；必须含协议及非默认端口 |
| `CODEATLAS_COOKIE_SECURE` | 本机 HTTP 开发为 false，生产 HTTPS 为 true |
| `CODEATLAS_ALLOW_ANONYMOUS_SEARCH` | 默认 false；只有已确认公开数据场景才启用 |
| `CODEATLAS_ALLOW_ANONYMOUS_CHAT` | 默认 false；与搜索开关分开管理 |
| `CODEATLAS_ALLOWED_GIT_HOSTS` | Git 允许主机；增加内网 Git 前评估网络访问策略 |
| `CODEATLAS_MCP_ALLOWED_HOSTS` | MCP 请求 Host 白名单，配置生产域名 |
| `CODEATLAS_ALLOWED_EXTERNAL_HOSTS` | 特定私有外部源的显式允许主机 |
| `CODEATLAS_EMBEDDING_*` | Embedding 类型、地址、模型、维度及凭据 |
| `CODEATLAS_LLM_*` | 问答模型与 OpenAI 兼容接口设置 |
| `CODEATLAS_BUILD_REVISION` | 健康接口中的部署版本标识 |

LLM 与 Embedding 配置可在 HTTPS 管理界面加密保存凭据，空字段表示保留原密钥，清除是独立操作。其解密依赖数据目录中的 `.llm-config.key`，必须与数据库共同备份。该设计是本地文件密钥加密，不是外部 KMS 或自动轮换。

Embedding Profile 使用模型和维度隔离向量集合。启用新 Profile 前应探测真实返回维度，安排重索引并检查任务状态，不把修改模型名称等同于旧向量已兼容新模型。

## 7 Windows 本地开发

准备 Git、Python 3.11、uv、Node.js 22、pnpm 和 MySQL 8.0。MySQL 需支持 `ngram` 全文解析器，业务数据库使用 `utf8mb4_0900_ai_ci`。下列密码占位符须替换为本地受保护配置，不使用示例值运行实际服务。

### 后端与初始账号

先在 MySQL 管理会话创建数据库和本地应用账号，并授予该数据库所需权限：

```sql
CREATE DATABASE codeatlas
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'codeatlas'@'127.0.0.1'
  IDENTIFIED BY 'REPLACE_WITH_LOCAL_DATABASE_PASSWORD';
GRANT ALL PRIVILEGES ON codeatlas.* TO 'codeatlas'@'127.0.0.1';
```

在 PowerShell 启动后端。直接访问 Vite 前端时，来源须设为 5173，不能照搬示例文件中的博客端口 4321：

```powershell
Set-Location D:\agent\CodeAtlas\backend
uv sync --frozen --python 3.11 --extra dev
$env:CODEATLAS_DATABASE_URL = 'mysql+pymysql://codeatlas:YOUR_LOCAL_DB_PASSWORD@127.0.0.1:3306/codeatlas?charset=utf8mb4'
$env:CODEATLAS_PUBLIC_ORIGIN = 'http://127.0.0.1:5173'
$env:CODEATLAS_COOKIE_SECURE = 'false'
uv run alembic upgrade head
uv run codeatlas create-admin --email owner@example.com --name Owner
uv run uvicorn codeatlas.app:create_app --factory --host 127.0.0.1 --port 8010
```

`create-admin` 在未提供密码时交互询问，不需要把管理员密码放进命令历史。已有管理员时不重复创建。演示数据可以通过 `seed-demo` 和 `index-demo` 载入，但企业试点更适合从已授权、可公开演示的少量仓库开始。

### 前端与博客

在独立 PowerShell 窗口启动前端：

```powershell
Set-Location D:\agent\CodeAtlas\frontend
pnpm install --frozen-lockfile
pnpm dev
```

打开 `http://127.0.0.1:5173/lab/code-kb/`。Vite 将 `/api/code-kb` 代理到后端 `/api/v1`；后端健康接口为 `http://127.0.0.1:8010/api/v1/health`。博客可在 `blog` 目录运行 `pnpm install --frozen-lockfile` 和 `pnpm dev`，默认地址为 `http://127.0.0.1:4321/`。

若指定端口已被占用，使用其他空闲端口，并同步调整后端来源配置及代理目标。不要同时使用 `localhost` 与 `127.0.0.1` 混淆 Cookie 和来源校验。Windows 中涉及原生 Chroma 的端到端运行仍需单独验证；本次本机验证遇到原生访问冲突，不能据此宣称已完成完整运行验收。

## 8 Linux 部署与发布

生产目标是 Linux 单机部署：Nginx 对外提供 HTTPS，Uvicorn 仅监听 `127.0.0.1:8010`。systemd 单 worker 配置包含 550 MB 软内存限制和 700 MB 硬限制，这些是现有资源约束，不是容量承诺。先在预发布环境用真实仓库规模和并发查询验证资源峰值。

发布前准备 MySQL 8、Nginx、systemd、Git、Python、TLS 证书和受保护环境文件 `/etc/codeatlas/codeatlas.env`。配置正式 HTTPS 来源、Secure Cookie 和 MCP Host。80 端口仅处理 ACME 验证与 HTTPS 跳转；兼容的 8080 入口只能跳转，不应代理应用。

安装脚本消费经过构建和核对的发布目录，必须包含 `backend/`、`frontend-dist/`、`blog-dist/`、`deploy/` 及 `RELEASE.json`。普通 Git 克隆目录不是可直接投产的发布包。脚本默认使用 `python3.12`，可由 `CODEATLAS_PYTHON_BIN` 指定符合项目版本要求的解释器。依赖锁变化需按脚本预检查处理，不能跳过检查强行安装。

```bash
sudo bash /root/codeatlas-release/deploy/install.sh /root/codeatlas-release
sudo systemctl status codeatlas --no-pager
sudo systemctl status nginx --no-pager
curl --fail http://127.0.0.1:8010/api/v1/health
curl --fail http://127.0.0.1:8010/api/v1/ready
sudo journalctl -u codeatlas -n 100 --no-pager
```

安装流程具有维护锁、候选版本准备、HTTPS 预检查、停机备份、迁移、健康验收与失败恢复。公网暴露前失败可恢复已验证的旧状态；暴露后如已有新写入，脚本关闭入口并保留新状态供人工恢复，避免覆盖已接收的数据。

GitHub Actions 的 `ci` 负责后端、前端、博客和密钥扫描质量检查，推送代码不会自动部署。`manual-production-release` 仅接受主分支手动触发，要求该次提交的主分支 CI 已成功，再构建带版本及校验和的发布包，通过受保护的 `DEPLOY_PASSWORD` 环境连接服务器。工作流包含目标主机核验、备份、安装和公网验收；发布后的 `health.revision` 应与 `RELEASE.json` 和该次工作流的提交一致。必须查看本次实际运行结果，不能复用上游已有部署的通过结论。

## 9 备份恢复与故障处理

### 备份范围与一致性

以 root 身份运行发布版本中的 `deploy/backup.sh`。默认读取 `/etc/codeatlas/codeatlas.env`，也可通过 `CODEATLAS_ENV_FILE` 指定其他受保护配置文件。备份脚本先加载环境，再确定数据、应用及备份目录，并使用维护锁避免与部署并行。

若服务原本运行，备份会短暂停止服务，使 MySQL 与 Chroma 对齐，结束时恢复原状态。归档包含 MySQL 逻辑备份、Chroma、文档原始字节、模型凭据加密密钥、环境文件、仓库清单，以及服务器上存在的博客源内容。归档默认位于 `/var/backups/codeatlas`，附 SHA-256 校验文件，不自动删除旧归档。

本次本地修复增加归档完整性检查：缺少 Chroma 目录会在停机前失败；数据库引用的文档原件必须存在于备份；已保存模型密文时，备份密钥必须能够解密这些密文。检查失败不能产生被视作成功的归档，并按原服务状态收尾。

Git 缓存、工作树和日志不在备份中。SSH 私钥及 `known_hosts` 也尚未由此脚本自动归档，须另行制定加密备份与恢复办法。原件与环境文件可能包含敏感信息，应限制备份存储访问并明确异地保存和保留周期。

```bash
sudo /root/codeatlas-release/deploy/backup.sh
# 使用其他环境文件时
sudo env CODEATLAS_ENV_FILE=/etc/codeatlas/staging.env \
  /root/codeatlas-release/deploy/backup.sh
```

### 恢复顺序

1. 确定目标环境和恢复点，在归档所在目录验证对应 `.sha256` 文件。
2. 关闭应用入口并停止服务，解压到新的暂存目录，不直接覆盖运行中的数据。
3. 恢复 MySQL、Chroma 和 `documents`，将 `provider-credentials.key` 恢复为数据目录中的 `.llm-config.key`，设置正确所有者和受限权限。
4. 审查并恢复环境配置，单独恢复需要的 SSH 凭据与主机信任信息；必要时恢复博客内容。
5. 启动服务，检查健康与就绪接口，再验证登录、私有仓库隔离、文档读取、模型解密和 MCP 查询。
6. 按 `repositories.json` 重新调度 Git 同步，重建缺失缓存与工作树；确认源码预览可用后再开放入口。

详细命令见 `deploy/RESTORE.md`。恢复会替换目标数据，应在维护窗口对明确的目标环境执行。SHA-256 只能证明归档未损坏，不能替代完整恢复演练。以演练测得的数据丢失窗口和恢复时间作为 RPO/RTO 依据，不预设无数据损失。

### 常见故障检查

无源码结果时先查活动索引与授权；索引排队时查任务状态和后台日志；模型错误时检查配置引用与密钥可解密性；备份失败时优先核对实际环境文件和数据目录。`ready` 可检查数据库与向量状态，但不保证 Git 远端、外部源和全部模型服务均正常。

## 10 验证标准与企业化路线

本手册说明预期行为，实际通过情况以随附的 `docs/optimization-validation-2026-09-06.zh-CN.md` 验证记录为准。验证记录分别列出命令、代码状态、通过数量、环境失败和未执行项目，不把不同批次的结果直接相加。当前记录包含 Windows 原生 Chroma 访问冲突和缺少 MySQL 测试服务的限制，尚不能标记完整端到端验收通过。

### 开发验证

```powershell
Set-Location D:\agent\CodeAtlas\backend
$env:CODEATLAS_TEST_DATABASE_URL = 'mysql+pymysql://TEST_USER:TEST_PASSWORD@127.0.0.1:3307/mysql?charset=utf8mb4'
uv run ruff check .
uv run mypy codeatlas
uv run pytest -q
Set-Location ..\frontend
pnpm lint
pnpm typecheck
pnpm test
pnpm build
Set-Location ..\blog
pnpm build
```

测试数据库账号需能创建和删除临时 `codeatlas_test_*` 数据库，不能指向生产数据库。并行测试须使用独立临时目录和数据库。Linux 部署相关脚本还应执行对应测试与实际预发布演练，mock 通过不能代替 Nginx、systemd 和真实数据库恢复验收。

### 企业试点验收

- 使用两名权限不同的账号验证搜索、文件、文档、聊天、Token 和 MCP；注销或切换账号后不显示前一账号数据。
- 让测试凭据经过实际索引、检索和源码预览，确认没有敏感残片；对历史库重新索引。
- 模拟 Embedding 服务不可用，核对受限词法降级；模拟 Commit 更新，确认旧引用返回 409。
- 执行成功及失败的备份恢复演练，核对文档原件、加密配置、SSH 凭据和活动索引。
- 以企业真实样本评估搜索命中率、引用准确度、延迟、同步时效和模型成本，记录目标值与实测值。

### 建设顺序

| 阶段 | 重点与验收产物 |
| --- | --- |
| 试点稳定性 | 完成本次修复的回归、Linux 运行验收、备份恢复演练与最小权限数据接入 |
| 企业身份与治理 | OIDC/SSO、企业群组及离职撤权、失败登录与敏感访问审计、Token 到期策略 |
| 数据权限与质量 | 外部源 ACL 同步、权限撤销清理、检索评测集、模型与成本观测 |
| 规模化运行 | 根据容量实测拆分后台任务，建立持久队列、共享限流、监控告警和容量规划 |
| 产品增强 | 在引用和版本体系稳定后，再实现自动 Wiki、代码地图和导览 |
| 多客户 SaaS | 只有明确需要服务多个企业时，建设租户成员关系、数据分区与跨租户隔离测试 |

参考入口：`README.zh-CN.md`、`docs/product-requirements.zh-CN.md`、`docs/codex-mcp.zh-CN.md`、`docs/operations.md`、`deploy/RESTORE.md` 及对应模块源码。规划文档中的需求不应自动视为当前已交付功能。
