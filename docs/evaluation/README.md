# Retrieval evaluation v0 (offline, bootstrap)

中文自然语言对照：参见 [结果与复验说明](natural-language-baseline.zh-CN.md)
和 `codeatlas-natural-language.json`。不要将带符号 smoke 样本的高分当成中文检索能力。

This is a **bootstrap/self-repository smoke baseline, not an unbiased benchmark**.
Seven answerable curated questions point to real CodeAtlas function definitions; most
include symbol names, deliberately favoring lexical retrieval. The eighth, absent-token
case is a synthetic empty-expected control.
A perfect score on this set does not establish retrieval quality, tenant isolation,
hybrid quality, LLM answer faithfulness, or production readiness.

## Run without services

From `D:/agent/CodeAtlas/backend` (Python 3.11+, standard library only at runtime):

```bash
python -m codeatlas.evaluation lexical \
  --dataset ../docs/evaluation/codeatlas-bootstrap.json --root .. --repo CodeAtlas \
  --source backend/codeatlas/ranking.py --source backend/codeatlas/retrieval.py \
  --source backend/codeatlas/security.py --source backend/codeatlas/chunker.py \
  --k 5 --output D:/hermes/codeatlas-lexical-run.json
python -m codeatlas.evaluation score \
  --dataset ../docs/evaluation/codeatlas-bootstrap.json \
  --run D:/hermes/codeatlas-lexical-run.json --k 5 \
  --output D:/hermes/codeatlas-evaluation-report.json
```

Output parents must exist. Omit `--output` to print JSON. Malformed input exits 2 with
an `error:` message; poor scores still exit 0 (no quality gate thresholds yet).
No database, network, settings, dotenv, embeddings or application initialization occurs.
The corpus is **only the explicitly named `.py` source files**: no directory scanning,
no fixtures, docs, dataset text or tests. Only supply approved non-secret source files.
Escaping paths and symlinks resolving outside the root are rejected.

The lexical runner splits source with stdlib AST function/method boundaries, computes
unique token overlap using existing `ranking.tokenize`, takes 50 candidates, and calls
existing `fuse_and_rerank` with an **empty vector list**. Existing per-file cap (2) and
overlap suppression remain active. This is not production MySQL lexical search,
production chunking, a hybrid search run or an LLM E2E evaluation. It emits
`mode: offline-source-lexical`, metadata-only hits (no source snippets), source-file
SHA-256 manifest, chunk count and query timings. Local working-tree content is evaluated;
no commit is invented. Timings exclude corpus reading, parsing and token preparation.

## Dataset and captured-run schema

Minimal dataset (illustrative schema, not an additional benchmark):

```json
{
  "queries": [
    {
      "id": "q1",
      "query": "Where is tokenization implemented?",
      "expected": [{"repo": "CodeAtlas", "path": "backend/codeatlas/ranking.py", "start_line": 12, "end_line": 29}],
      "forbidden": [{"repo": "private-repo", "path": "src/internal.py"}]
    },
    {"id": "q2", "query": "Absent feature", "expected": []}
  ]
}
```

Corresponding captured-run shape (**illustrative, not measured output**):

```json
{
  "mode": "authorized-hybrid-capture",
  "results": [
    {"id": "q1", "latency_ms": 23.4, "hits": [{"repo": "CodeAtlas", "path": "backend/codeatlas/ranking.py", "start_line": 12, "end_line": 29, "retrieval": "hybrid"}]},
    {"id": "q2", "hits": []}
  ]
}
```

For future **authorized** live retrieval, separately capture each query's actual ordered
`CodeRetriever.search` result dictionaries into `hits` (or adapt the real API response).
Keep repo identifiers, canonical relative POSIX paths, commit and line provenance exactly
as returned. Match the dataset's repo naming convention explicitly. Do not manufacture
hits, fill failed/missing requests with `[]`, or infer a successful refusal from an error.
The evaluator does not invoke the retriever, check credentials, or authorize capture.
`mode` is supplied provenance, not independently verified by the scorer. Keep user/scope,
backend revision, corpus revision and retrieval settings in your capture metadata;
extra fields are allowed. Preserve degraded/fallback flags and don't call such a run hybrid.

Validation requires nonempty unique query IDs, exact dataset/run ID correspondence,
nonempty query text, explicit expected and hits arrays, valid evidence objects and
finite nonnegative optional latency. Missing, extra or duplicate IDs, duplicate JSON
keys, NaN/Infinity, invalid JSON and missing files fail, rather than score as refusals.
Optional `commit` is an exact nonempty string match (use full hashes when available).
Line bounds must occur together as positive inclusive integers with start <= end.
The `symbol` labels in the bootstrap dataset are documentation/test anchors, not a
scoring filter; the test checks their AST definitions and exact line ranges for drift.

## Metric definitions

- Evidence matches exact `repo` + `path`, plus exact commit when specified in the target.
  If the target includes a line range, the hit must have at least one inclusive line in
  common. Missing hit commit/lines cannot satisfy those target constraints.
- `hit_rate_at_k`: fraction of answerable queries with any matched target in first k hits.
- `recall_at_k`: macro mean of matched expected targets / total expected targets per
  answerable query. Repeated hits cannot count a target twice. Curate distinct targets;
  overlapping targets can both match one broad result, so prefer narrow evidence ranges.
- `mrr_at_k`: mean reciprocal first relevant rank over answerable queries; misses are zero.
  Metrics are `null` if there are no answerable queries (not vacuous perfect scores).
- `forbidden_leak_count`: returned hit occurrences matching any forbidden target, across
  **all returned hits**, including beyond k. Each hit counts once even if multiple
  forbidden targets match. This is only labeled-evidence checking, not an ACL/security test.
- `unanswered_query_ids`: explicit empty hit lists, regardless of expected answerability.
  `expected_empty_returned_empty` counts empty-expected/empty-returned cases separately;
  it is not a refusal correctness metric. Per-query `cases` retain misses and leaks.
- Optional latency reports count, mean and maximum only for supplied timings; no invented
  zero timings for missing values. Scoring reports hash both input files for provenance.

## Verification and first measured result

```bash
.venv/Scripts/python.exe -m pytest --noconftest tests/test_evaluation.py -q
.venv/Scripts/python.exe -m ruff check codeatlas/evaluation.py tests/test_evaluation.py
.venv/Scripts/python.exe -m ruff format --check codeatlas/evaluation.py tests/test_evaluation.py
```

`--noconftest` is intentional: existing root test fixtures import the application and
provision MySQL on port 3307. This evaluation suite is isolated and must not use them.
Full integration tests were **not** run; no access to ports 3306/3307 is needed.
TDD execution observed failures before metric implementation, validation, CLI and dataset,
then green tests. Initial real four-file run: 8 queries / 7 answerable, k=5;
hit rate=1.0, macro recall=1.0, MRR=1.0, forbidden leaks=0, one explicit empty result
(`absent-token-control`). These easy in-corpus results are only a usable plumbing baseline.
Generated run/report are external under `D:/hermes`, not checked-in results.

Next benchmark work should independently curate held-out repositories, natural-language
paraphrases, hard negatives, multi-evidence cases, revision changes and permission-scoped
queries, then capture real authorized hybrid/degraded runs. No such result is claimed here.
