# Architecture Decisions

## Versioned Indexes

An index job creates a new `IndexGeneration`. Chroma and MySQL code-chunk rows include
the generation identifier. CodeAtlas switches `Repository.active_generation_id`
only after vector and lexical writes succeed. Failed generations are removed
from both stores and never become queryable.

Git previews follow the same model. An incremental shallow cache is updated,
then Git creates an immutable worktree for the job. The database points file
preview at the worktree associated with the active index, preventing a failed
sync from showing files from a different commit.

## Retrieval

1. Resolve the upper-bound repository set from browser RBAC or MCP Token scope.
2. Apply requested repository, language and path filters.
3. Retrieve vector Top 50 and MySQL `FULLTEXT` Top 50.
4. Fuse candidates using weighted Reciprocal Rank Fusion with `k=60`.
5. Add a small path and symbol coverage adjustment.
6. Suppress line-overlapping chunks and cap each file at two results.
7. Return at most ten code evidence records.

Uploaded document and Wiki retrieval is owned by `KnowledgeSearch`. Browser and MCP
adapters apply identity checks, then call the same query validation, filtering,
ranking and result formatting implementation.

## Runtime

The production process has one Uvicorn worker and one indexing thread. MySQL 8
stores business state and lexical code indexes; Chroma stores vectors. The
single indexing thread avoids resource spikes and same-repository races on a
2 GB server. On startup, jobs left in `running` state are returned to `queued`
and resubmitted.

`IndexJobQueue` is the only production path that creates index jobs. It locks the
repository row before checking the active-job invariant, commits each job before
submitting it to the indexing thread, and leaves committed queued jobs available
for startup recovery after a process failure.

`SourcePollingCoordinator` owns polling schedules and queue policy. GitHub and
GitLab are separate remote adapters. Each source respects its configured polling
interval, and a failed provider cycle is logged without stopping the other
provider or future cycles.

## Trust Boundaries

| Boundary | Controls |
|---|---|
| Browser | Argon2id, server-side session, HttpOnly cookie, Origin check, CSRF |
| MCP HTTP | Bearer Token digest lookup, expiry/revocation, scopes, repository IDs |
| Git network | HTTPS only, host allowlist, DNS public-address check, no credentials |
| Git content | no submodules/LFS, shallow branch, file count and byte limits |
| Indexed content | assignment and PEM redaction before embedding/storage |
| File reads | active authorized repository, resolved path containment, 200 lines/64 KB |

## Deliberate Omissions

The first release does not include AI chat, write-capable MCP tools, repository
webhooks, Redis, PostgreSQL, MinIO or Milvus. Those additions are justified only
after multi-process indexing, larger corpora or high availability become real
requirements. A vector backend interface should be introduced only when a
second working adapter creates a real seam.
