# CodeAtlas 产品需求文档

| 项目 | 内容 |
|---|---|
| 文档状态 | Draft |
| PRD 版本 | 0.1 |
| 产品阶段 | 原型验证 / 内部测试 |
| 目标形态 | 企业内部代码 RAG、Codex MCP 知识源、自动代码 Wiki |
| 更新日期 | 2026-09-04 |

## 1. 产品摘要

CodeAtlas 面向需要理解、复用和传承内部代码知识的研发团队。产品连接 GitHub、GitLab、项目文档和外部知识源，将代码转换为可检索、可引用、可解释的知识，并通过 Web 控制台和只读 MCP 接口提供给开发者与 Codex 等编码代理。

本阶段不以完整企业交付为目标，而是优先验证三个产品闭环：

1. 开发者能否通过 Codex 查询公司已有实现，并在新项目中遵循团队代码风格。
2. 系统能否基于一个固定 Commit 自动生成可信、易导航的代码 Wiki。
3. 代码地图和引导式导览能否降低理解陌生仓库的时间。

系统需要保留企业化演进接口，但当前只实现单 Workspace、本地账号和只读 Token，不在原型阶段引入完整 SSO、SCIM、多租户或分布式基础设施。

## 2. 背景与问题

企业代码知识通常分散在仓库、Wiki、文档、人员经验和历史项目中，存在以下问题：

- 新项目难以找到公司已有的组件、目录组织和工程规范。
- 开发者知道“公司有类似实现”，但不知道应该搜索哪个仓库或文件。
- 通用编码代理能生成代码，却不了解企业内部约定和既有技术决策。
- 传统 Wiki 容易过期，无法证明描述对应哪个 Commit 和哪段源码。
- 大型仓库缺少模块关系图和推荐阅读顺序，新成员上手成本高。
- 权限往往只存在于 Git 平台，进入 RAG 后容易丢失原有访问边界。

## 3. 产品定位

CodeAtlas 不是通用代码托管平台，也不是替代 IDE 的代码浏览器。它是企业代码之上的知识与理解层：

```text
Git 与项目文档
    -> 安全同步和结构化分析
    -> 可授权的代码知识空间
    -> 混合检索与可验证引用
    -> Codex MCP / Web 搜索 / 自动 Wiki / 代码地图
```

### 3.1 产品原则

1. 静态分析确定事实，LLM 负责组织和解释。
2. 所有重要结论必须可追溯到仓库、Commit、路径、符号和行号。
3. 权限在召回前执行，不能先检索全部内容再过滤。
4. 自动生成失败不能影响当前已发布 Wiki 和在线代码检索。
5. MCP 默认只读，不能修改远端仓库或触发未经授权的操作。
6. 原型允许单机运行，但领域模型不能阻断未来的企业身份和权限接入。
7. 公开演示环境与未来企业内部环境必须使用独立数据和部署。

## 4. 用户角色

| 角色 | 当前阶段需求 | 后续企业化需求 |
|---|---|---|
| 系统管理员 | 配置模型、仓库、外部源和 Token | 对接身份源、审计、全局策略 |
| 空间管理员 | 选择参考仓库、触发索引和 Wiki 生成 | 管理空间成员和内容策略 |
| 开发者 | 搜索代码、使用 MCP、阅读 Wiki 和导览 | 使用个人身份和团队权限访问 |
| 新成员 | 快速理解系统结构和推荐阅读路径 | 获取与岗位匹配的知识空间 |
| 编码代理 | 通过只读工具查询规范和实现 | 使用用户委托身份及细粒度授权 |

原型阶段允许系统管理员兼任空间管理员。

## 5. 核心使用场景

### 5.1 Codex 学习公司代码风格

开发者在本地创建官网或业务项目。Codex 在开始实现前调用 CodeAtlas MCP：

1. 获取当前任务对应的公司工程规范。
2. 查找两个或更多参考实现。
3. 读取相关文件的必要片段。
4. 总结目录、组件、命名、样式、错误处理和测试模式。
5. 在本地项目中实现代码并运行本地验证。
6. 在交付说明中列出采用的内部规范和参考来源。

CodeAtlas 不要求把目标项目上传到服务端；本地代码仍由 Codex 的本地执行环境处理。

### 5.2 自动生成仓库 Wiki

用户选择已导入仓库，或输入受支持的公开 GitHub/GitLab 仓库地址。系统在索引完成后分析当前 Commit，生成 Wiki 目录、页面、图表、代码引用、代码地图和导览。

### 5.3 理解陌生系统

用户从“系统概览”进入代码地图，查看模块依赖，选择一条导览路线，并逐步打开入口、核心服务、数据模型和外部集成对应的源码。

## 6. 范围与优先级

| 优先级 | 功能 | 阶段目标 |
|---|---|---|
| P0 | 稳定的 Git 索引与混合检索 | 复用现有能力并修复权限范围传递 |
| P0 | Codex Streamable HTTP MCP | 验证本地开发者真实使用闭环 |
| P0 | 公司工程规范 | 输出经过确认、带源码引用的规范 |
| P0 | 单仓库自动 Wiki | 生成结构化页面、引用和 Mermaid 图 |
| P0 | Wiki Generation 版本管理 | 固定 Commit、校验后原子发布 |
| P0 | 模块级交互式代码地图 | 浏览模块、入口和主要依赖关系 |
| P0 | 基础引导式代码导览 | 提供可复用的新成员阅读路线 |
| P0 | 默认知识空间与统一授权接口 | 统一仓库、文档、Wiki 和 MCP 范围 |
| P1 | 精细空间权限管理 | 空间成员、角色、群组预留和管理界面 |
| P1 | 符号级代码地图 | 按需展开文件、类、函数和调用关系 |
| P1 | 增量 Wiki 更新 | 根据 Git Diff 定位受影响页面 |
| P2 | OIDC、SCIM、企业群组 | 正式企业化阶段实现 |
| P2 | 分布式任务与水平扩容 | 规模验证后实现 |

## 7. 功能需求

### 7.1 仓库接入与索引

#### FR-REPO-01 仓库输入

- 已导入仓库可以通过名称直接选择。
- 新仓库支持输入完整 GitHub/GitLab URL 或 `owner/repository`。
- 私有仓库必须显式配置 Deploy Key 或服务端凭据引用。
- 用户可以选择分支；每次分析必须解析为不可变 Commit。

#### FR-REPO-02 语言支持等级

- A 级深度支持：Java、Python、JavaScript、TypeScript。
- B 级基础支持：已识别扩展名的文件级切块、目录结构和全文检索。
- 二进制、依赖目录、构建产物和超限文件不进入代码分析。
- 页面必须展示本次生成的语言覆盖情况与未深度解析范围。

#### FR-REPO-03 索引一致性

- 继续使用独立 Index Generation。
- 向量、MySQL 全文记录和工作树全部成功后才能激活。
- 失败任务保留错误摘要，不影响上一活动版本。

### 7.2 公司工程规范

#### FR-STYLE-01 参考仓库

- 管理员可按语言、框架和业务类型指定一个或多个参考仓库。
- 可以设置主参考仓库及辅助参考仓库。
- 普通历史项目不能自动成为公司规范来源。

#### FR-STYLE-02 规范内容

规范至少覆盖：

- 目录与模块组织。
- 文件、变量、组件、类和 API 命名。
- UI 组件与样式组织。
- API 请求、状态管理和错误处理。
- 日志、配置、密钥和环境变量处理。
- 测试文件位置、命名和断言风格。
- 推荐模式与明确禁止模式。

#### FR-STYLE-03 证据与冲突

- 每条规范必须包含至少一个源码或配置文件引用。
- 多仓库模式冲突时，主参考仓库优先。
- 系统不能从一次偶然写法推导强制规范。
- 管理员可以确认、编辑、废弃和重新生成规范。
- 人工确认内容与自动推断内容必须有明确状态区分。

#### FR-STYLE-04 MCP 工具

新增：

```text
get_company_conventions(
    language,
    framework,
    task,
    space_id?
)
```

返回：适用范围、规则、禁止模式、示例摘要、仓库/Commit/路径/行号引用和更新时间。

现有 `list_repositories`、`search_code`、`grep_code`、`find_references`、`get_file`、`search_documents`、`search_wiki`、`search_knowledge` 保持兼容。

#### FR-STYLE-05 Codex 使用指导

- 文档提供用户级和项目级 MCP 配置示例。
- 提供可放入项目 `AGENTS.md` 的团队规范查询模板。
- MCP Server instructions 应指示代理先获取规范，再查找具体实现。
- Token 只能通过环境变量或凭据助手注入，示例不得把明文 Token 写入配置文件。

### 7.3 自动 Wiki

#### FR-WIKI-01 生成入口

- 仓库详情页提供“生成 Wiki”操作。
- 首版只支持手动生成，不在每次 Git 同步后自动调用 LLM。
- 用户可以选择生成语言、目标受众和生成深度。
- 同一仓库同一时间只能存在一个活动生成任务。

#### FR-WIKI-02 生成流水线

任务状态包括：

```text
queued
-> analyzing
-> planning
-> generating
-> validating
-> published | failed
```

流水线必须执行：

1. 锁定 Repository、Index Generation 和 Commit。
2. 识别语言、框架、构建文件、入口和顶层模块。
3. 构建模块、文件、符号及依赖关系。
4. 为每个主题生成有大小限制的证据包。
5. 先生成目录，再逐页面生成内容。
6. 校验引用、内部链接、图表和代码地图节点。
7. 所有必需页面通过后原子发布 Wiki Generation。

#### FR-WIKI-03 默认页面

默认生成：

- 项目概览。
- 快速开始与本地运行。
- 技术栈与构建系统。
- 目录和模块结构。
- 系统架构。
- 核心请求或任务数据流。
- API、CLI 和其他入口点。
- 数据模型与持久化。
- 后台任务和外部集成。
- 安全与权限边界。
- 测试与发布方式。
- 关键模块说明。
- 新成员推荐阅读路线。

只在仓库存在充分证据时生成对应页面。不能为了满足模板而编造不存在的层次。

#### FR-WIKI-04 引用规则

- 架构结论、流程描述和代码行为必须带引用。
- 引用包含 repository、commit、path、symbol、start_line、end_line。
- 发布前验证路径和行号属于固定 Commit。
- 引用打开时重新执行当前用户权限校验。
- 页面必须展示“基于哪个 Commit 生成”。

#### FR-WIKI-05 生成控制

- 设置单仓库最大文件数、最大证据量、页面数和 LLM 调用预算。
- 超限时生成范围报告，不静默跳过。
- 仓库内容作为不可信数据处理，不能覆盖系统提示或生成规则。
- LLM 请求和错误日志不得包含 API Key、Token 或未经脱敏的凭据。

### 7.4 可视化图表

#### FR-DIAGRAM-01 Mermaid 图

- 支持系统上下文、模块依赖、请求流程、任务流程和数据模型图。
- 图表优先由静态分析结果生成结构，再由 LLM 补充可读标签。
- 发布前进行 Mermaid 语法校验。
- 图中节点必须能链接到 Wiki 页面或代码地图实体。

#### FR-DIAGRAM-02 图表降级

- 图表渲染失败时仍显示文本说明和节点列表。
- 图表错误不能导致整个已生成 Wiki 不可访问。

### 7.5 交互式代码地图

#### FR-MAP-01 图模型

节点类型至少包括：模块、目录、文件、类、接口、函数和入口点。

边类型至少包括：包含、导入、调用、继承、实现和数据访问。

#### FR-MAP-02 交互

- 搜索节点并按语言、模块和节点类型筛选。
- 展开一跳或两跳关系。
- 切换依赖方向。
- 聚焦节点并查看摘要、引用和源码。
- 大图默认按模块聚合，不能一次渲染全部符号。
- 首选 Cytoscape.js 或同类成熟图引擎；分层布局可使用 ELK.js。

### 7.6 引导式代码导览

#### FR-TOUR-01 默认导览

首版提供：

- 15 分钟项目总览。
- 从入口到核心业务流程。
- 数据如何进入和离开系统。
- 后台任务与异步处理。
- 新增一个典型功能应该修改哪些模块。

#### FR-TOUR-02 导览步骤

每个步骤包含标题、解释、代码地图焦点、源码引用、上一步和下一步。导览必须固定到一个 Wiki Generation 和 Commit。

### 7.7 权限与身份

#### FR-AUTH-01 当前实现范围

- 当前阶段继续支持本地账号、HttpOnly Session、CSRF 和 Bearer Token。
- MCP Token 默认只读，具有有效期、撤销状态和访问范围。
- 禁止多人共享同一个管理员 Token。

#### FR-AUTH-02 企业化预留

新增统一的 `AuthorizationScope` 内部接口，包含允许的 space、repository、collection 和 action。浏览器、MCP、文件预览和聊天必须使用同一个授权结果。

领域模型预留：

```text
Workspace
KnowledgeSpace
SpaceGrant
Group
GroupMember
SpaceGroupGrant
ServicePrincipal
TokenSpaceGrant
```

原型阶段只启用单 Workspace 和默认 Knowledge Space；OIDC、SCIM 和群组同步不实现，但业务服务不得依赖“用户一定来自本地密码表”的假设。

#### FR-AUTH-03 内容继承

- Repository、DocumentCollection 和 Wiki Generation 必须属于 Knowledge Space。
- 外部知识源继承目标 DocumentCollection 的空间权限。
- 自动 Wiki、代码地图和导览不能获得比来源仓库更宽的权限。
- 用户失去空间权限后不能继续读取相关 Wiki、源码引用或历史导览。

#### FR-AUTH-04 检索授权

- 先计算授权范围，再向 MySQL 和 Chroma 发起查询。
- Chroma metadata 包含 `space_id`，使用 `where` 下推过滤。
- MySQL FULLTEXT 在 SQL 查询阶段限制允许资源。
- 修复 MCP `search_knowledge` 对私有仓库 scope 传递不完整的问题。
- 语言和路径过滤应尽量在候选召回阶段执行，而不是 Top K 之后执行。

### 7.8 管理与可观测性

- 查看索引、Wiki 生成和规范生成任务状态。
- 查看阶段耗时、文件数、Chunk 数、LLM 调用数和失败原因。
- 支持取消尚未进入发布阶段的生成任务。
- 审计授权变更、Token 生命周期、Wiki 发布和文件读取。
- 搜索词和代码正文默认不进入审计详情。

## 8. 信息架构

Web 控制台建议调整为：

```text
检索工作台
├── 统一搜索
├── 代码问答
└── 代码浏览

知识空间
├── 空间概览
├── 仓库
├── 文档与外部源
├── 公司工程规范
└── 成员与权限

代码知识
├── 自动 Wiki
├── 代码地图
└── 引导式导览

平台管理
├── 索引与生成任务
├── Embedding 模型
├── LLM Provider
├── Token
└── 审计
```

## 9. 建议数据模型

### 9.1 权限边界

```text
KnowledgeSpace
- id
- workspace_id
- name
- description
- visibility: workspace | restricted

SpaceGrant
- space_id
- user_id
- role: manager | editor | viewer
```

### 9.2 Wiki 版本

```text
RepositoryWiki
- id
- repository_id
- active_generation_id

WikiGeneration
- id
- repository_id
- index_generation_id
- commit
- status
- language
- audience
- model
- progress
- error
- created_at
- activated_at

GeneratedWikiPage
- id
- generation_id
- slug
- title
- parent_slug
- body_markdown
- sort_order

WikiCitation
- page_id
- repository_id
- commit
- path
- symbol
- start_line
- end_line
```

### 9.3 代码地图与导览

```text
CodeNode
- generation_id
- kind
- qualified_name
- path
- symbol
- metadata_json

CodeEdge
- generation_id
- source_node_id
- target_node_id
- kind

CodeTour
- generation_id
- title
- description

CodeTourStep
- tour_id
- step_order
- title
- content
- focus_node_ids_json
- citation_ids_json
```

原型阶段使用 MySQL 存储节点和边，不引入图数据库。确认大型仓库性能不足后再评估专用图存储。

## 10. 接口要求

### 10.1 REST

建议新增：

```text
POST   /api/v1/repositories/{id}/wiki-generations
GET    /api/v1/repositories/{id}/wiki-generations
GET    /api/v1/wiki-generations/{id}
POST   /api/v1/wiki-generations/{id}/cancel
GET    /api/v1/repositories/{id}/wiki
GET    /api/v1/repositories/{id}/wiki/pages/{slug}
GET    /api/v1/repositories/{id}/code-map
GET    /api/v1/repositories/{id}/code-tours
GET    /api/v1/code-tours/{id}
GET    /api/v1/company-conventions
POST   /api/v1/company-conventions/generate
PATCH  /api/v1/company-conventions/{id}
```

所有新增接口都必须使用统一 AuthorizationScope。生成接口要求 `editor` 或 `manager`，读取接口要求 `viewer`。

### 10.2 MCP

建议新增：

```text
get_company_conventions
get_repository_wiki_outline
get_repository_wiki_page
get_code_map_summary
get_code_tour
```

MCP 工具保持只读，不提供“生成 Wiki”“同步仓库”或“修改规范”工具。写操作继续只允许在受 CSRF 保护的管理控制台执行。

## 11. 非功能需求

### 11.1 安全

- 代码和仓库文档均视为不可信输入，防止 Prompt Injection。
- 凭据在索引、日志和 LLM 上下文前脱敏。
- Token 明文只显示一次，服务端仅保存摘要。
- Provider 密钥加密保存或通过服务端凭据引用解析。
- URL、DNS、分支、归档和文件路径继续执行现有安全检查。
- 任何 Wiki 和代码地图缓存键都必须包含授权边界与 generation。

### 11.2 可靠性

- 生成任务状态持久化，进程重启后 queued/running 任务可恢复或明确失败。
- 发布采用 generation 原子切换。
- 所有派生数据都能够根据 MySQL 事实数据和固定 Git Commit 重建。
- 删除和重建操作必须幂等。

### 11.3 性能与成本

- 检索请求不得触发 Wiki 或规范生成。
- 静态分析结果复用现有 Index Generation。
- LLM 采用分层、分模块证据包，不能上传整个仓库上下文。
- 每个生成任务必须有页面数、Token、调用次数和并发上限。
- 代码地图首屏只加载模块级图，符号按需加载。

### 11.4 可解释性

- 生成页面展示模型、Commit、生成时间和覆盖范围。
- 自动推断、人工确认和源码事实具有不同标识。
- 无法验证的结论不得发布为事实。

## 12. 原型验证方案

### 12.1 测试仓库集合

至少准备：

- 一个 Vue/TypeScript 官网或后台项目。
- 一个 Python/FastAPI 服务。
- 一个 Java/Spring 服务。
- 一个多模块或前后端混合仓库。

每个仓库由熟悉代码的开发者提供参考答案。

### 12.2 评测任务

- 找到公司按钮、表单、API Client 和错误处理模式。
- 让 Codex 按参考风格实现一个新页面。
- 找出系统入口和一次核心请求的完整链路。
- 生成 Wiki 并核对引用是否支持正文结论。
- 使用代码地图找到模块依赖和关键入口。
- 新成员仅通过 Wiki 和导览完成指定代码定位任务。
- 使用无权限 Token 验证不能搜索、读取或通过引用访问受限内容。

## 13. 成功指标

| 指标 | 原型目标 |
|---|---|
| 已发布引用路径和 Commit 有效率 | 100% |
| 必需 Wiki 页面生成成功率 | 不低于 90% |
| Mermaid 语法校验通过率 | 100% 后才发布 |
| MCP 只读工具调用成功率 | 不低于 99%（排除上游网络故障） |
| 权限隔离自动化测试 | 0 条越权结果 |
| Codex 风格任务人工通过率 | 不低于 80% |
| 新成员代码定位时间 | 相比无 CodeAtlas 基线降低 30% |
| Wiki 结论人工抽检准确率 | 不低于 90% |

指标在首轮基线测试后允许调整，但引用有效率和越权结果属于硬门槛。

## 14. MVP 验收标准

MVP 必须完成以下端到端验收：

1. 管理员导入一个受支持仓库并成功完成代码索引。
2. 开发者在本地 Codex 中通过 HTTPS MCP 和独立 Token 连接 CodeAtlas。
3. Codex 能获取公司规范、搜索参考实现并读取授权源码片段。
4. Codex 根据检索证据完成一个本地实现，输出采用的规范和参考路径。
5. 用户能为仓库手动创建 Wiki Generation。
6. Wiki 包含导航、架构说明、至少一张有效 Mermaid 图和源码引用。
7. 用户能打开模块级代码地图并从节点跳转到 Wiki 或源码。
8. 用户能完成一条至少五步的引导式导览。
9. 重新生成失败时，上一版本 Wiki 继续可用。
10. 无权限用户和 Token 无法通过搜索、Wiki、地图、导览或引用访问内容。

## 15. 暂不实现

- 自动修改、提交或合并远端仓库代码。
- 写能力 MCP 工具。
- 每次 Push 自动生成完整 Wiki。
- 对所有编程语言提供同等深度的调用图。
- OAuth 多租户、SCIM 和完整企业组织同步。
- Kubernetes、Redis、Celery、独立向量数据库集群或图数据库。
- 跨企业共享知识空间。
- 将 LLM 生成内容视为无需审核的正式工程规范。

## 16. 迭代路线

### 阶段 A：权限与 MCP 基线

- 引入默认 Knowledge Space 和 AuthorizationScope。
- 修复 REST/MCP 统一权限范围。
- 保持现有 MCP 工具兼容。
- 增加公司规范数据与 `get_company_conventions`。
- 完成本地 Codex 真实项目测试。

### 阶段 B：自动 Wiki

- 增加 Wiki Generation、页面、引用和任务编排。
- 实现静态分析、目录规划、分页面生成和原子发布。
- 实现 Mermaid 校验和 Wiki 导航。

### 阶段 C：代码地图与导览

- 增加 CodeNode、CodeEdge 和模块级图接口。
- 实现交互式地图和源码联动。
- 实现默认导览模板及生成流程。

### 阶段 D：增量与质量

- 使用 Git Diff 计算受影响模块和页面。
- 建立自动评测集、质量门槛和人工反馈闭环。
- 根据测试结果扩展语言和框架分析器。

### 阶段 E：企业化

- 接入 OIDC、SCIM、企业群组和服务身份。
- 增加正式审计、合规保留和管理员策略。
- 根据容量数据拆分持久任务 Worker 和共享向量服务。

## 17. 主要风险与应对

| 风险 | 应对 |
|---|---|
| LLM 生成错误架构结论 | 静态事实优先、强制引用、发布前校验和人工抽检 |
| 大仓库生成成本不可控 | 模块化证据包、预算上限、分级生成和增量更新 |
| 历史项目风格冲突 | 指定参考仓库、人工确认规范、显示适用范围 |
| 权限在统一检索中丢失 | 统一 AuthorizationScope，并在存储层过滤 |
| 代码内容包含 Prompt Injection | 仓库内容标记为不可信，不允许覆盖系统规则 |
| 图规模过大导致前端不可用 | 模块聚合、按需展开、节点和边数量上限 |
| 进程内任务不适合规模化 | 原型保留单 Worker，企业化阶段迁移持久任务系统 |
| 公司代码进入外部模型上下文 | 明确数据策略、可配置 Provider、最小证据传输和脱敏 |

## 18. 待评审决策

1. 原型阶段首个主参考仓库及支持的前端框架。
2. 公司规范是否必须由管理员确认后才能供 MCP 使用。
3. Wiki 首版默认语言和最大页面数量。
4. 代码调用关系首版需要达到文件级、类级还是函数级。
5. 测试阶段使用的 LLM Provider、数据保留和费用上限。
6. 默认 Knowledge Space 是否允许所有已登录测试用户访问。
7. Wiki 生成任务是否允许普通 editor 触发，或暂时仅限管理员。
