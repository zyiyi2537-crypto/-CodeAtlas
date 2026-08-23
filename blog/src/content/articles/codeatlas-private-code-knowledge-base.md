---
title: 从零构建企业级私有代码知识库与 MCP 检索服务
description: CodeAtlas 如何在一台小型服务器上完成仓库同步、版本化索引、混合检索、权限隔离和 MCP 工具调用。
publishedAt: 2026-08-17
tags: [架构设计, FastAPI, MCP]
featured: true
---

企业代码搜索真正困难的部分不是“把代码放进向量库”，而是让每条结果都能回答四个问题：它来自哪个仓库、哪个提交、哪一段代码，以及当前调用者为什么有权看到它。

CodeAtlas 把这些问题放在同一个系统边界里处理。它不是聊天应用，而是一个可独立验证的检索层：网页端返回代码证据，MCP 端把同一套证据交给支持工具调用的客户端。

## 约束先于技术选择

项目部署在资源有限的单机环境中，因此首版明确排除了 Redis、PostgreSQL、MinIO 和 Milvus。这个限制促成了更清晰的职责划分：

- MySQL 8 保存用户、仓库、权限、任务、索引版本和审计事件。
- MySQL FULLTEXT 与 ngram parser 提供词法召回。
- Chroma PersistentClient 保存代码块向量。
- 单线程执行器串行处理索引任务，避免两个大仓库同时争夺内存。
- Nginx 只暴露静态站点、REST 反向代理和带 Token 的 MCP 入口。

这里的目标不是追求最大吞吐量，而是在约 700 MB 的进程硬限制内保持行为可预测。

## 一次索引如何完成

### 1. 受控同步

管理员只能提交 HTTPS Git URL。服务会检查主机白名单、禁止内嵌凭据，并解析 DNS 结果阻止私网和回环地址。分支名也必须通过引用格式约束，避免它被 Git 当作命令选项。

每个仓库维护一个单分支浅克隆缓存。同步时先增量抓取最新提交，再建立不可变工作树。当前在线索引仍指向旧工作树，所以新的同步过程不会让文件预览和旧提交错位。

### 2. 解析、切片与脱敏

Java、Python、JavaScript 和 TypeScript 通过 Tree-sitter 查找类、方法与函数边界。较大的语法节点继续按行数和字符数切分，并保留少量重叠上下文。Markdown、YAML、SQL 等文本格式则使用限长窗口。

每个代码块都会携带以下定位信息：

```text
repository_id + generation_id + commit
path + language + symbol
start_line + end_line
```

Embedding 前会移除常见密码、Token、API Key 赋值和 PEM 私钥块。脱敏不是权限控制的替代品，但它能降低索引意外收录凭据后的暴露范围。

### 3. 版本化激活

新一代索引首先写入 Chroma，然后在一个 MySQL 事务中写入带 FULLTEXT 索引的代码块记录。只有两边都成功后，仓库的 `active_generation_id` 才会指向新版本。

如果解析、Embedding 或写入任一步失败，任务会清理这一代 Chroma 和 FTS 数据，并继续使用上一个活动版本。这个策略把“索引刷新失败”和“线上搜索不可用”拆成了两个独立事件。

## 检索不是只取最近向量

一次搜索分别取向量 Top 50 和 MySQL FULLTEXT Top 50，再通过 Reciprocal Rank Fusion 融合：

```text
score(d) = Σ weight(source) / (60 + rank(source, d))
```

向量召回擅长意图相近的实现，词法召回擅长精确符号、错误码和配置键。融合之后，CodeAtlas 还会根据路径与符号对查询词的覆盖率进行来源感知重排，并限制同一文件的结果数量、抑制高度重叠的片段。

最终结果最多十条，每条都返回融合分数、向量分数、词法分数和命中来源。分数不是“代码正确率”，它只是可解释的排序信号。

## 同一权限模型服务 REST 与 MCP

浏览器登录使用 Argon2id、服务端 Session、HttpOnly Cookie 和 CSRF Token。管理员可以管理成员、仓库、任务与 API Token，普通成员只能看到公开仓库和明确授权的私有仓库。

MCP Token 的明文只在创建时显示一次，数据库仅保存 SHA-256 摘要。每个 Token 包含独立工具权限和仓库范围。Streamable HTTP 在请求进入 MCP SDK 之前完成 Bearer Token 校验，并把身份放入请求上下文。

CodeAtlas 暴露六个 MCP 工具：

- `list_repositories`
- `search_code`
- `grep_code`
- `get_file`
- `find_references`
- `index_status`

`get_file` 会再次检查仓库范围、路径穿越和符号链接逃逸，且单次最多返回 200 行或 64 KB。工具调用不会因为已经拿到搜索结果就绕过文件访问边界。

## 关键工程决策

CodeAtlas 的工程价值体现在以下可验证、可运维的系统决策：

1. 用版本切换隔离索引失败对在线查询的影响。
2. 用混合召回解决语义搜索对精确标识符不稳定的问题。
3. 让网页与 MCP 共享数据边界，但使用适合各自客户端的认证方式。
4. 把 Git SSRF、路径逃逸、秘密脱敏和 Token 范围当作核心功能，而不是上线前补丁。

受控公开实例只索引许可证宽松的小型开源仓库，用于产品评估与接口验证；企业私有部署的代码和向量数据不会进入公开环境。
