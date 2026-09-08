"""Dependency-free retrieval evaluation; never initializes the application or services."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

from .ranking import fuse_and_rerank, tokenize


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _evidence_list(items: object, label: str) -> None:
    if not isinstance(items, list):
        raise ValueError(f"{label} must be an explicit list")
    for item in items:
        if not isinstance(item, dict) or not all(
            _nonempty(item.get(key)) for key in ("repo", "path")
        ):
            raise ValueError(f"{label}: evidence requires nonempty repo/path")
        path = item["path"]
        if "\\" in path or ":" in path or any(p in {"", ".", ".."} for p in path.split("/")):
            raise ValueError(f"{label}: path must be canonical repository-relative POSIX")
        if "commit" in item and not _nonempty(item["commit"]):
            raise ValueError(f"{label}: commit must be nonempty")
        if "start_line" in item or "end_line" in item:
            start, end = item.get("start_line"), item.get("end_line")
            if type(start) is not int or type(end) is not int or not 1 <= start <= end:
                raise ValueError(f"{label}: invalid inclusive line range")


def _rows(document: object, key: str) -> dict:
    if not isinstance(document, dict) or not isinstance(document.get(key), list):
        raise ValueError(f"{key} must be a list")
    rows = {}
    for row in document[key]:
        if not isinstance(row, dict) or not _nonempty(row.get("id")):
            raise ValueError(f"{key}: each row requires a nonempty id")
        if row["id"] in rows:
            raise ValueError(f"{key}: duplicate query ID {row['id']}")
        rows[row["id"]] = row
    return rows


def _validate_dataset(dataset: dict) -> dict:
    queries = _rows(dataset, "queries")
    if not queries:
        raise ValueError("queries must not be empty")
    for query in queries.values():
        if not _nonempty(query.get("query")):
            raise ValueError("query must be nonempty")
        _evidence_list(query.get("expected"), "expected")
        _evidence_list(query.get("forbidden", []), "forbidden")
    return queries


def _matches(expected: dict, hit: dict) -> bool:
    if any(expected[key] != hit.get(key) for key in ("repo", "path")):
        return False
    if "commit" in expected and expected["commit"] != hit.get("commit"):
        return False
    if "start_line" in expected:
        return (
            "start_line" in hit
            and "end_line" in hit
            and max(expected["start_line"], hit["start_line"])
            <= min(expected["end_line"], hit["end_line"])
        )
    return True


def evaluate(dataset: dict, run: dict, k: int = 5) -> dict:
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    queries = _validate_dataset(dataset)
    by_id = _rows(run, "results")
    if queries.keys() != by_id.keys():
        raise ValueError(
            f"query IDs mismatch: missing={sorted(queries.keys() - by_id.keys())}, "
            f"extra={sorted(by_id.keys() - queries.keys())}"
        )
    for row in by_id.values():
        _evidence_list(row.get("hits"), "hits")
        if "latency_ms" in row:
            latency = row["latency_ms"]
            try:
                finite_latency = math.isfinite(latency)
            except (OverflowError, TypeError):
                finite_latency = False
            if type(latency) not in (int, float) or not finite_latency or latency < 0:
                raise ValueError("latency_ms must be finite and nonnegative")
    cases = []
    latencies = []
    for query in dataset["queries"]:
        row = by_id[query["id"]]
        hits, expected = row["hits"], query["expected"]
        ranks = [
            next((rank for rank, hit in enumerate(hits[:k], 1) if _matches(target, hit)), None)
            for target in expected
        ]
        found = [rank for rank in ranks if rank is not None]
        cases.append(
            {
                "id": query["id"],
                "answerable": bool(expected),
                "unanswered": not hits,
                "hit_at_k": int(bool(found)),
                "recall_at_k": len(found) / len(expected) if expected else None,
                "rr_at_k": 1 / min(found) if found else 0.0,
                "forbidden_leak_count": sum(
                    any(_matches(target, hit) for target in query.get("forbidden", []))
                    for hit in hits
                ),
            }
        )
        if "latency_ms" in row:
            latencies.append(row["latency_ms"])
    positive = [case for case in cases if case["answerable"]]
    latency_summary = None
    if latencies:
        try:
            latency_total = math.fsum(latencies)
        except OverflowError as exc:
            raise ValueError("latency_ms aggregate must be finite") from exc
        if not math.isfinite(latency_total):
            raise ValueError("latency_ms aggregate must be finite")
        latency_summary = {
            "count": len(latencies),
            "mean": latency_total / len(latencies),
            "max": max(latencies),
        }
    return {
        "k": k,
        "query_count": len(cases),
        "answerable_count": len(positive),
        "hit_rate_at_k": sum(c["hit_at_k"] for c in positive) / len(positive) if positive else None,
        "recall_at_k": sum(c["recall_at_k"] for c in positive) / len(positive)
        if positive
        else None,
        "mrr_at_k": sum(c["rr_at_k"] for c in positive) / len(positive) if positive else None,
        "forbidden_leak_count": sum(c["forbidden_leak_count"] for c in cases),
        "unanswered_query_ids": [c["id"] for c in cases if c["unanswered"]],
        "expected_empty_returned_empty": sum(
            not c["answerable"] and c["unanswered"] for c in cases
        ),
        "latency_ms": latency_summary,
        "cases": cases,
    }


def lexical_run(dataset: dict, root: Path, repo: str, sources: list[str], k: int = 5) -> dict:
    """Read only explicitly allowed Python source files; AST functions are the corpus."""
    queries = _validate_dataset(dataset)
    if type(k) is not int or k < 1 or not _nonempty(repo):
        raise ValueError("positive k and nonempty repo required")
    root = root.resolve()
    corpus: list[dict] = []
    manifest: list[dict] = []
    if not sources or len(set(sources)) != len(sources):
        raise ValueError("source files must be nonempty and unique")
    for relative in sorted(sources):
        _evidence_list([{"repo": repo, "path": relative}], "source")
        path = root / relative
        if path.suffix != ".py" or not path.resolve().is_relative_to(root):
            raise ValueError("source must be a repository-contained Python file")
        raw = path.read_bytes()
        source = raw.decode("utf-8-sig")
        manifest.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
        lines = source.splitlines()
        tree = ast.parse(source, filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            document = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            metadata = {
                "repo": repo,
                "path": relative,
                "symbol": node.name,
                "start_line": node.lineno,
                "end_line": node.end_lineno,
                "language": "python",
            }
            corpus.append(
                {
                    "id": f"{relative}:{node.lineno}",
                    "metadata": metadata,
                    "document": document,
                    "tokens": set(tokenize(f"{relative} {node.name} {document}")),
                }
            )
    if not corpus:
        raise ValueError("source corpus contains no Python function definitions")
    results = []
    for query in queries.values():
        started = perf_counter()
        tokens = set(tokenize(query["query"]))
        candidates = []
        for item in corpus:
            score = len(tokens & item["tokens"]) / max(1, len(tokens))
            if score:
                candidates.append(
                    {key: value for key, value in item.items() if key != "tokens"}
                    | {"lexical_score": score}
                )
        candidates.sort(key=lambda item: (-item["lexical_score"], item["id"]))
        hits = fuse_and_rerank(query["query"], [], candidates[:50], k)
        # Captures contain evidence metadata, not source text or potential source secrets.
        for hit in hits:
            hit.pop("snippet", None)
        results.append(
            {"id": query["id"], "hits": hits, "latency_ms": (perf_counter() - started) * 1000}
        )
    return {
        "mode": "offline-source-lexical",
        "corpus": manifest,
        "chunk_count": len(corpus),
        "results": results,
    }


def _json_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _load(path: Path) -> dict:
    return json.loads(
        path.read_text(encoding="utf-8-sig"),
        object_pairs_hook=_json_object,
        parse_constant=_invalid_constant,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("score", "lexical"):
        command = commands.add_parser(name)
        command.add_argument("--dataset", type=Path, required=True)
        command.add_argument("--k", type=int, default=5)
        command.add_argument("--output", type=Path)
        if name == "score":
            command.add_argument("--run", type=Path, required=True)
        else:
            command.add_argument("--root", type=Path, required=True)
            command.add_argument("--repo", required=True)
            command.add_argument("--source", action="append", required=True)
    args = parser.parse_args()
    try:
        dataset = _load(args.dataset)
        if args.command == "score":
            run = _load(args.run)
            result = evaluate(dataset, run, args.k)
            result["mode"] = run.get("mode", "captured-results-unspecified")
            result["dataset_sha256"] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
            result["run_sha256"] = hashlib.sha256(args.run.read_bytes()).hexdigest()
        else:
            result = lexical_run(dataset, args.root, args.repo, args.source, args.k)
        rendered = json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except (ValueError, OSError, SyntaxError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
