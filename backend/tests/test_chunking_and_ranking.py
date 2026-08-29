from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.chunker import chunk_file
from codeatlas.embeddings import EmbeddingClient
from codeatlas.ranking import fuse_and_rerank, rerank_across_source_types, tokenize
from codeatlas.settings import Settings


@pytest.mark.parametrize(
    ("filename", "source", "symbol"),
    [
        ("Demo.java", "class Demo { int answer() { return 42; } }", "answer"),
        ("demo.py", "class Demo:\n    def answer(self):\n        return 42\n", "answer"),
        ("demo.js", "function answer() { return 42; }", "answer"),
        ("demo.ts", "export function answer(): number { return 42; }", "answer"),
    ],
)
def test_tree_sitter_chunks_supported_languages(
    tmp_path: Path, filename: str, source: str, symbol: str
) -> None:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    chunks = chunk_file(path, tmp_path, "repo", "generation", "abc123")
    assert chunks
    assert any(symbol in chunk.symbol for chunk in chunks)
    assert all(chunk.path == filename for chunk in chunks)


def test_chunking_redacts_secrets(tmp_path: Path) -> None:
    path = tmp_path / "config.py"
    path.write_text(
        "def settings():\n    password = hunter2\n    return password\n",
        encoding="utf-8",
    )
    content = "\n".join(
        chunk.content
        for chunk in chunk_file(path, tmp_path, "repo", "generation", "abc")
    )
    assert "hunter2" not in content
    assert "[REDACTED]" in content


def test_tree_sitter_objects_remain_alive_for_large_files(tmp_path: Path) -> None:
    path = tmp_path / "serializer.py"
    path.write_text(
        "\n\n".join(
            f"def serialize_{index}(value):\n    return str(value)"
            for index in range(120)
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path, tmp_path, "repo", "generation", "abc")
    assert any(chunk.symbol == "serialize_119" for chunk in chunks)


def test_hash_embedding_is_deterministic_and_normalized(settings: Settings) -> None:
    embedder = EmbeddingClient(settings)
    first, second = embedder.embed(["Spring request mapping", "Spring request mapping"])
    assert first == second
    assert len(first) == settings.embedding_dimension
    assert sum(value * value for value in first) == pytest.approx(1.0)


def candidate(identifier: str, start: int, source: str) -> dict:
    return {
        "id": identifier,
        "document": f"function searchCode at line {start}",
        "metadata": {
            "repo": "repo",
            "path": "src/search.ts",
            "symbol": "searchCode",
            "language": "typescript",
            "start_line": start,
            "end_line": start + 10,
        },
        f"{source}_score": 0.9,
    }


def test_rrf_marks_hybrid_hits_and_suppresses_overlap() -> None:
    vector = [candidate("same", 10, "vector"), candidate("overlap", 12, "vector")]
    lexical = [candidate("same", 10, "lexical")]
    results = fuse_and_rerank("searchCode", vector, lexical, 10)
    assert results[0]["retrieval"] == "hybrid"
    assert len(results) == 1
    assert "searchcode" in tokenize("searchCode")


def test_cross_source_rrf_does_not_compare_incompatible_raw_scores() -> None:
    results = rerank_across_source_types(
        [
            {"id": "code-1", "source_type": "code", "score": 0.001},
            {"id": "code-2", "source_type": "code", "score": 0.0009},
            {"id": "doc-1", "source_type": "document", "score": 0.99},
            {"id": "wiki-1", "source_type": "wiki", "score": 0.4},
        ],
        limit=4,
    )

    by_id = {item["id"]: item for item in results}
    assert by_id["code-1"]["source_rank"] == 1
    assert by_id["doc-1"]["source_rank"] == 1
    assert by_id["wiki-1"]["source_rank"] == 1
    assert by_id["code-1"]["score"] == by_id["doc-1"]["score"]
    assert by_id["code-2"]["score"] < by_id["code-1"]["score"]
    assert by_id["doc-1"]["source_score"] == 0.99
    assert [item["id"] for item in results[:3]] == ["code-1", "doc-1", "wiki-1"]
