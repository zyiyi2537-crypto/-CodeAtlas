# CodeAtlas demo fixtures

This directory contains small, public, deterministic fixtures for local demos and tests.

It intentionally does **not** contain:

- MySQL or SQLite database files
- Chroma vector indexes
- browser sessions, API tokens, or encrypted provider keys
- cloned repository worktrees or Git metadata

The repository manifest lists the public repositories used by the demo seed command.
Documents are sanitized copies of public, non-secret documentation fixtures.

To create the same demo repository definitions in a local database:

```bash
cd backend
uv run codeatlas seed-demo
```

To index them, configure a local MySQL database and run:

```bash
uv run codeatlas index-demo
```
