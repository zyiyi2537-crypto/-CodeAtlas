# 中文自然语言对照集

`codeatlas-natural-language.json` 与 `codeatlas-bootstrap.json` 使用相同源码范围，但不在问题里提示目标符号。期望证据根据当前源码预先编写，没有为提高分数调整标签。

首次实际执行离线词法流程：6 个中文问题，Hit@5、Recall@5、MRR@5 均为 0，全部未命中。带符号 bootstrap 的 7 个有答案问题三项均为 1；另有一个明确不存在词的空结果控制。

这说明这套简单 token 重叠离线基线对中文问题与英文实现之间的语义对应无能为力，不能说明生产混合检索也为 0。没有调用 MySQL、Chroma、Embedding 或 LLM，也没有执行权限隔离评测。耗时只包含进程内查询排序，不含源码读取/索引构建/网络。

复验（backend 目录，结果目录须已存在）：

```sh
uv run python -m codeatlas.evaluation lexical --dataset ../docs/evaluation/codeatlas-natural-language.json --root .. --repo CodeAtlas --source backend/codeatlas/ranking.py --source backend/codeatlas/retrieval.py --source backend/codeatlas/security.py --source backend/codeatlas/chunker.py --output D:/hermes/codeatlas-iteration1-natural-run.json
uv run python -m codeatlas.evaluation score --dataset ../docs/evaluation/codeatlas-natural-language.json --run D:/hermes/codeatlas-iteration1-natural-run.json --output D:/hermes/codeatlas-iteration1-natural-report.json
```

下一步把相同问题提交给经过授权的实际混合检索接口，按既定格式保存真实排序与仓库 ID，统一映射仓库身份后评分。禁止以人工期望证据伪造实际返回结果。该对照不是模型答案评测，也不是独立外部 benchmark。
