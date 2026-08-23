---
title: 混合代码检索：为什么向量搜索还需要 FULLTEXT 与 RRF
description: 从标识符、自然语言和代码路径三类查询出发，拆解 CodeAtlas 的双路召回、RRF 融合与重叠抑制。
publishedAt: 2026-08-17
tags: [检索架构, MySQL FULLTEXT, RRF]
featured: false
---

代码查询通常混合了三种语言：自然语言意图、精确标识符和文件路径。只使用向量搜索时，“找到处理认证失败的逻辑”表现不错，但 `ERR_TOKEN_EXPIRED`、`UserSession.token_hash` 或 `src/auth/session.py` 这类查询经常不稳定。

CodeAtlas 使用双路召回。向量路径理解语义，MySQL FULLTEXT 路径保留精确词法信号，二者在重排阶段才汇合。

## 两条召回路径

向量路径把查询转换为与索引一致维度的 Embedding，从当前活动索引版本中取 50 个候选。生产配置可以连接 OpenAI-compatible Embedding API；本地和测试使用确定性的 1024 维哈希 Embedding，因此不依赖外部密钥也能复现流程。

词法路径先拆分驼峰、下划线、路径分隔符和中文双字片段，再将有效词项交给 MySQL 8 的 ngram 全文解析器：

```sql
SELECT c.*,
       MATCH(c.path, c.symbol, c.content)
       AGAINST (? IN BOOLEAN MODE) AS lexical_rank
FROM codechunkrecord c
WHERE c.generation_id IN (...)
  AND MATCH(c.path, c.symbol, c.content)
      AGAINST (? IN BOOLEAN MODE)
ORDER BY lexical_rank DESC
LIMIT 50;
```

`generation_id` 过滤非常关键。数据库可以同时保留历史版本，但搜索只能读取仓库当前激活的那一代。

## 用排名而不是原始分数融合

向量距离和 BM25 值没有共同尺度。直接归一化相加会把结果质量绑在某个向量模型、语料分布或查询长度上。

Reciprocal Rank Fusion 只使用各路候选的排名：

```text
rrf(d) = 1.0 / (60 + vector_rank)
       + 0.9 / (60 + lexical_rank)
```

同一代码块如果被两路同时召回，会自然获得更高分。词法路径权重略低，但对精确命中的贡献仍然稳定。由于 RRF 对尾部排名不敏感，切换 Embedding 模型时不必重新手工标定两套分数范围。

## 来源感知重排

RRF 之后还需要处理代码特有的问题。

### 路径与符号覆盖

查询词如果直接出现在路径或符号名中，结果获得轻量加权。例如查询 `createBrowserSession` 时，函数定义应排在仅仅调用它的通用认证片段之前。

### 重叠抑制

Tree-sitter 节点和限长窗口可能覆盖相近行区间。如果两个候选来自同一仓库、同一文件，且短片段至少 20% 的行与另一个结果重叠，后出现的候选会被跳过。

### 文件多样性

每个文件最多保留两条结果。这样一次搜索不会被一个包含大量相似方法的工具类占满，调用者能更快看到控制器、服务和测试之间的不同证据。

## 过滤必须发生在授权之后

仓库、语言和路径过滤不能替代权限过滤。CodeAtlas 先根据浏览器用户或 MCP Token 得到允许的仓库集合，再从其中选择请求指定的仓库，最后才应用语言和路径前缀。

顺序反过来会产生一个常见缺陷：请求者显式传入未授权的仓库 ID 后，后端直接按 ID 查询，绕过了默认的公开仓库集合。授权集合必须始终是查询范围的上界。

## 结果要能回到证据

混合检索最终返回的不只是代码文本，还包含提交号、路径、符号、起止行、融合得分、两路原始分数和命中来源。网页可以据此打开限定范围的文件预览，MCP 客户端也可以用 `get_file` 二次读取周边上下文。

对代码知识库而言，召回只是第一步。让结果保持版本一致、权限正确并可回到原始证据，才是检索服务可以进入真实开发流程的前提。
