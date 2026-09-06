# CodeAtlas 首批优化与验证记录

日期：2026-09-06。基线：`4a3cae9ed46dad759e1f6419bcead9e1c75c8b52`。位置：`D:\agent\CodeAtlas`。本文记录首批优化在本地验证阶段的结果，当时尚未提交、推送或部署到 `atcode.asia`。后续 Linux 验证和服务器发布结果以仓库 Actions 记录为准。

## 已完成改动

| 领域 | 新行为 | 兼容边界 |
| --- | --- | --- |
| 浏览器身份隔离 | 身份切换清理查询与操作缓存、取消在途请求并重建页面；旧响应不能覆盖新账号，旧 401 不注销新账号 | 本地缓存边界加固，不代替服务端授权 |
| 敏感信息脱敏 | 检测与替换共用规则，合并重叠匹配，覆盖常见厂商凭据、连接串、自然语言声明，保留换行与字段结构 | 历史索引需重建；不是覆盖所有秘密的 DLP 系统 |
| 源码版本 | REST 与 MCP 文件读取接受 `commit`；授权后发现非当前版本则拒绝，REST 返回 409；前端缓存与聊天引用携带 Commit | 不传 Commit 的旧客户端继续读取当前版本，不新增永久历史版本存储 |
| 问答引用 | 仅返回实际进入模型上下文的证据；超预算片段不产生引用编号，仍可选择后续较短片段 | 不代表已实现模型回答逐句事实验证 |
| 检索故障恢复 | 查询 Embedding 单次尝试、5 秒 HTTP 超时设置；网络/超时/远端协议故障及 HTTP 408、429、5xx 使用已授权全文结果 | 401/403、无效协议、请求配置和向量维度错误继续失败；不处理任意 Chroma/数据库故障 |
| 降级标识 | 每条降级结果包含 `degraded=true`、`degradation_reason=embedding_unavailable` | 结果为空时仍是 `[]`；未新增前端专门提示 |
| 来源同步 | 数据库短暂异常不会永久结束轮询；来源失败隔离，60 秒可中断等待后继续 | 仍为单机进程内任务，不是分布式租约 |
| 一致性备份 | 读取环境后解析路径；缺失 Chroma、原文或无法解密的密钥阻止产生成功归档 | 仍需停服务；未自动实现异地加密保存，SSH 私钥另行保护 |
| 文档与工程 | 增加项目说明书、验证记录，更新架构文档；忽略并行测试临时目录 | 不更换框架或依赖锁，不改变数据库结构 |

## 实际验证结果

验证使用项目锁定依赖，后端运行于隔离 Python 3.11 环境，前端使用 Node.js 与 pnpm。以下统计按测试批次列出，存在重复执行的用例，不能简单相加为全量覆盖。

| 验证 | 结果 |
| --- | --- |
| 前端 ESLint | 通过 |
| 前端 TypeScript 与生产构建 | 通过；Vite 主 JS 约 329.49 kB，gzip 约 107.93 kB |
| 前端全量 Vitest | 18 文件、96 项通过，使用串行 worker 和 20 秒单例超时；项目默认测试配置未改 |
| 后端 Ruff 全项目 | 通过 |
| 后端 Mypy | 40 个源码文件通过，有既有未标注函数体检查提示，无类型错误 |
| 安全、分块、检索恢复、Embedding 协议、来源轮询 | 最终合并执行 138 项通过、1 项显式排除；排除项需要尚未运行的 MySQL fixture |
| 备份配置、来源轮询、既有部署检查 | 28 项通过，其中 4 项轮询与上一批重复 |
| Bash 语法和差异空白检查 | 通过 |
| 本地浏览器 | 前端登录页可加载，桌面页面已截图检查；手机 390 宽度下 DOM 未产生横向滚动，侧栏动画结束后位于屏幕外 |

首次前端并发执行在本机负载下出现原有聊天测试随机超时，串行复查全部通过；没有通过放宽项目默认配置来隐藏断言失败。

Windows 的备份测试只对 POSIX `install/chmod` 权限操作使用替身，复制、打包和读取归档真实执行；数据库和 systemctl 使用测试替身。Linux CI 仍使用原始权限命令，这不等同于真实生产灾备演练。

## 未通过或未执行的验证

- 本机没有可用 MySQL 测试服务。已有 `test_hash_embedding_is_deterministic_and_normalized` 使用数据库相关 settings fixture，初次执行因连接拒绝报错，最终明确排除该用例；未篡改 fixture 将它假装通过。
- `test_knowledge_chunks_share_profile_collection_with_source_filters` 在 Chroma 原生 `_upsert` 中发生 Windows `access violation`；独立用例在沙箱外复查仍然崩溃。这条路径中的 `vector_store.py` 未被本次修改，当前结论是本机原生依赖运行存在阻碍，尚未在 Linux 验证。
- 已安装的 Docker Desktop 未能启动引擎，日志包含 WSL 超时及本地组件路径访问错误。没有重置 Docker、修改虚拟化设置或使用线上数据库代替测试库。
- 因上述限制，没有完成全部后端测试、真实数据库迁移演练、真实向量写入端到端验收和容量压测。不能将已通过的定向回归等同于完整生产发布门禁通过。
- 这批改动未触发 GitHub Actions，也未运行博客构建；博客代码未修改。
- 本地预览仅启动前端，后端未启动，不能在该预览中完成真实登录、索引或问答。
- 说明书交付为 Markdown 和自包含 HTML。当前环境缺少允许使用的 Word 渲染组件；HTML 本地文件的浏览器导航被工具 URL 安全策略拦截。已做内容与结构核查，没有宣称 Word 分页或 HTML 完整视觉验收通过。

## 复验命令

前端，在 `frontend` 目录：

```text
pnpm lint
pnpm typecheck
pnpm test --maxWorkers=1 --testTimeout=20000
pnpm build
```

后端定向检查，在 `backend` 目录：

```text
uv run ruff check .
uv run mypy codeatlas
uv run pytest tests/test_security.py tests/test_chunking_and_ranking.py tests/test_retrieval_resilience.py tests/test_tencent_multimodal_embedding.py tests/test_source_polling_recovery.py -q -k "not test_hash_embedding_is_deterministic_and_normalized"
uv run pytest tests/test_source_polling_recovery.py tests/test_backup_configuration.py tests/test_deploy_https.py -q
```

Windows 运行 Bash 相关测试时，应使用 Git Bash 并保证其 `/usr/bin` 和 `/bin` 在路径中；本机临时 `BASH_ENV` 文件只用于测试工具路径，不是生产配置。

完整发布前，在 Linux 预发布环境配置专用 `CODEATLAS_TEST_DATABASE_URL`，使用 MySQL 8.0 与 ngram，并执行：

```text
uv sync --frozen --extra dev --python 3.11
uv run pytest -q
uv run ruff check .
uv run mypy codeatlas
```

随后演练“登录与账号切换、授权检索、索引更新后的旧引用、模型限流降级、自定义路径备份与恢复”。只有这些结果通过后，才使用现有经过保护的发布流程将本次代码部署到服务器。新脱敏规则需要对现有仓库及文档安排重建；发布本身不自动完成历史数据治理。
