"""Offline tests: run pytest --noconftest to avoid application/DB initialization."""

import ast
import copy
import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


def evaluator():
    assert importlib.util.find_spec("codeatlas.evaluation"), "evaluation module not implemented"
    return importlib.import_module("codeatlas.evaluation")


def evidence(path="a.py", **extra):
    return {"repo": "demo", "path": path, **extra}


def dataset():
    return {
        "queries": [
            {
                "id": "q1",
                "query": "find answer",
                "expected": [evidence(commit="abc", start_line=10, end_line=20), evidence("b.py")],
                "forbidden": [evidence("secret.py")],
            },
            {"id": "q2", "query": "unknown", "expected": []},
            {"id": "q3", "query": "missing", "expected": [evidence()]},
        ]
    }


def test_metrics_use_rank_commit_inclusive_overlap_and_explicit_empty_runs():
    run = {
        "results": [
            {
                "id": "q1",
                "hits": [
                    evidence(commit="wrong", start_line=10, end_line=20),
                    evidence(commit="abc", start_line=20, end_line=30),
                    evidence("secret.py"),
                ],
                "latency_ms": 12,
            },
            {"id": "q2", "hits": []},
            {"id": "q3", "hits": []},
        ]
    }
    report = evaluator().evaluate(dataset(), run, k=2)
    assert report["hit_rate_at_k"] == 0.5
    assert report["recall_at_k"] == 0.25
    assert report["mrr_at_k"] == 0.25
    assert report["forbidden_leak_count"] == 1  # all returned hits, not only top-k
    assert report["unanswered_query_ids"] == ["q2", "q3"]
    assert report["expected_empty_returned_empty"] == 1
    assert report["latency_ms"] == {"count": 1, "mean": 12.0, "max": 12.0}


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"results": []},
        {"results": [{"id": "extra", "hits": []}]},
        {"results": [{"id": "q1", "hits": []}] * 3},
        {"results": [{"id": "q1"}, {"id": "q2", "hits": []}, {"id": "q3", "hits": []}]},
    ],
)
def test_invalid_run_ids_or_missing_hits_fail_clearly(bad):
    with pytest.raises(ValueError, match="results|IDs|duplicate|hits"):
        evaluator().evaluate(dataset(), bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("repo", ""),
        ("path", "../escape.py"),
        ("path", "/a.py"),
        ("start_line", 0),
        ("start_line", True),
        ("end_line", 2),
        ("commit", ""),
    ],
)
def test_malformed_evidence_is_rejected(field, value):
    data = copy.deepcopy(dataset())
    data["queries"][0]["expected"][0][field] = value
    run = {"results": [{"id": q["id"], "hits": []} for q in data["queries"]]}
    with pytest.raises(ValueError):
        evaluator().evaluate(data, run)


@pytest.mark.parametrize(
    "latency",
    [-1, True, float("nan"), float("inf"), "12", 10**400],
)
def test_invalid_latency_is_rejected(latency):
    run = {
        "results": [
            {"id": q["id"], "hits": [], "latency_ms": latency} for q in dataset()["queries"]
        ]
    }
    with pytest.raises(ValueError, match="latency"):
        evaluator().evaluate(dataset(), run)


def test_latency_summary_rejects_a_nonfinite_aggregate():
    data = {
        "queries": [
            {"id": "q1", "query": "first", "expected": []},
            {"id": "q2", "query": "second", "expected": []},
        ]
    }
    run = {
        "results": [
            {"id": "q1", "hits": [], "latency_ms": 1e308},
            {"id": "q2", "hits": [], "latency_ms": 1e308},
        ]
    }
    with pytest.raises(ValueError, match="latency"):
        evaluator().evaluate(data, run)


def test_duplicate_dataset_ids_and_nonpositive_k_rejected():
    data = dataset()
    run = {"results": [{"id": q["id"], "hits": []} for q in data["queries"]]}
    for k in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="k"):
            evaluator().evaluate(data, run, k)
    data["queries"].append(data["queries"][0])
    with pytest.raises(ValueError, match="duplicate"):
        evaluator().evaluate(data, run)


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "codeatlas.evaluation", *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_scores_captured_runs_and_rejects_invalid_json(tmp_path):
    data = tmp_path / "queries.json"
    run = tmp_path / "run.json"
    data.write_text(json.dumps(dataset()), encoding="utf-8")
    run.write_text(
        json.dumps({"results": [{"id": q["id"], "hits": []} for q in dataset()["queries"]]}),
        encoding="utf-8",
    )
    result = cli("score", "--dataset", data, "--run", run, "--k", 3)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "CLI must emit a real report"
    assert json.loads(result.stdout)["query_count"] == 3
    for text in ('{"results": [], "results": []}', "{bad", '{"results": NaN}'):
        run.write_text(text, encoding="utf-8")
        result = cli("score", "--dataset", data, "--run", run)
        assert result.returncode == 2
        assert "error:" in result.stderr
        assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "latencies",
    [
        [10**400, None, None],
        [1e308, 1e308, None],
    ],
)
def test_cli_rejects_nonfinite_latency_inputs_and_aggregates(tmp_path, latencies):
    data = tmp_path / "queries.json"
    run = tmp_path / "run.json"
    data.write_text(json.dumps(dataset()), encoding="utf-8")
    results = []
    for query, latency in zip(dataset()["queries"], latencies, strict=True):
        row = {"id": query["id"], "hits": []}
        if latency is not None:
            row["latency_ms"] = latency
        results.append(row)
    run.write_text(json.dumps({"results": results}), encoding="utf-8")

    result = cli("score", "--dataset", data, "--run", run)

    assert result.returncode == 2
    assert "latency" in result.stderr
    assert "Traceback" not in result.stderr


def test_offline_cli_uses_only_explicit_source_corpus_and_shared_lexical_ranking(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "answer.py").write_text("def uniqueAnswer():\n    return 42\n", encoding="utf-8")
    data = tmp_path / "queries.json"
    data.write_text(
        json.dumps(
            {
                "queries": [
                    {"id": "q", "query": "uniqueAnswer", "expected": [evidence("answer.py")]},
                    {"id": "empty", "query": "zzzznotpresent", "expected": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "run.json"
    result = cli(
        "lexical",
        "--dataset",
        data,
        "--root",
        root,
        "--repo",
        "demo",
        "--source",
        "answer.py",
        "--output",
        output,
    )
    assert result.returncode == 0, result.stderr
    assert output.exists(), "CLI must capture actual lexical hits"
    run = json.loads(output.read_text(encoding="utf-8"))
    assert run["mode"] == "offline-source-lexical"
    assert run["results"][0]["hits"][0]["retrieval"] == "lexical"
    assert run["results"][0]["hits"][0]["start_line"] == 1
    assert run["results"][1]["hits"] == []
    assert "commit" not in run["results"][0]["hits"][0]
    assert evaluator().evaluate(json.loads(data.read_text()), run)["hit_rate_at_k"] == 1
    for source in ("../escape.py", ".env"):
        result = cli(
            "lexical", "--dataset", data, "--root", root, "--repo", "demo", "--source", source
        )
        assert result.returncode == 2


def test_bootstrap_labels_point_to_actual_symbol_definitions():
    root = Path(__file__).resolve().parents[2]
    path = root / "docs/evaluation/codeatlas-bootstrap.json"
    assert path.is_file(), "curated bootstrap dataset not implemented"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["benchmark_type"] == "bootstrap-self-repository"
    for query in data["queries"]:
        for target in query["expected"]:
            tree = ast.parse((root / target["path"]).read_text(encoding="utf-8"))
            assert any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == target["symbol"]
                and node.lineno == target["start_line"]
                and node.end_lineno == target["end_line"]
                for node in ast.walk(tree)
            )


def test_natural_language_labels_remain_real_source_ranges():
    root = Path(__file__).resolve().parents[2]
    data = json.loads(
        (root / "docs/evaluation/codeatlas-natural-language.json").read_text(encoding="utf-8")
    )
    for query in data["queries"]:
        for target in query["expected"]:
            nodes = ast.walk(ast.parse((root / target["path"]).read_text(encoding="utf-8")))
            assert any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.lineno == target["start_line"]
                and node.end_lineno == target["end_line"]
                for node in nodes
            ), query["id"]
