from __future__ import annotations

import re
from collections import Counter

RRF_K = 60
MAX_RESULTS_PER_FILE = 2
_SEGMENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$./:-]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _SEGMENT_RE.finditer(text):
        segment = match.group(0)
        if "\u4e00" <= segment[0] <= "\u9fff":
            normalized = segment.lower()
            tokens.append(normalized)
            tokens.extend(normalized[index : index + 2] for index in range(len(normalized) - 1))
            continue
        expanded = _CAMEL_BOUNDARY_RE.sub(" ", segment)
        normalized = segment.lower()
        tokens.append(normalized)
        tokens.extend(
            part.lower()
            for part in re.split(r"[^A-Za-z0-9]+|_+", expanded)
            if part and part.lower() != normalized
        )
    return tokens


def fuse_and_rerank(
    query: str,
    vector_candidates: list[dict],
    lexical_candidates: list[dict],
    limit: int,
) -> list[dict]:
    query_tokens = set(tokenize(query))
    pool: dict[str, dict] = {}
    for source, weight, candidates in (
        ("vector", 1.0, vector_candidates),
        ("lexical", 0.9, lexical_candidates),
    ):
        for rank, candidate in enumerate(candidates, start=1):
            item = pool.setdefault(
                str(candidate["id"]),
                {**candidate, "rrf": 0.0, "sources": set()},
            )
            item["rrf"] += weight / (RRF_K + rank)
            item["sources"].add(source)
            for key in ("vector_score", "lexical_score"):
                item[key] = max(float(item.get(key, 0)), float(candidate.get(key, 0)))
    ranked = []
    for item in pool.values():
        metadata = item["metadata"]
        context_tokens = set(tokenize(f"{metadata.get('path', '')} {metadata.get('symbol', '')}"))
        informative = {token for token in query_tokens if len(token) > 1}
        coverage = len(informative & context_tokens) / len(informative) if informative else 0
        quality = 1.08 if metadata.get("language") in {"java", "python", "typescript"} else 1.0
        item["rank_score"] = (item["rrf"] + 0.002 * coverage) * quality
        ranked.append(item)
    ranked.sort(key=lambda item: item["rank_score"], reverse=True)

    selected: list[dict] = []
    per_file: Counter[tuple[str, str]] = Counter()
    for item in ranked:
        metadata = item["metadata"]
        file_key = (str(metadata.get("repo", "")), str(metadata.get("path", "")))
        if per_file[file_key] >= MAX_RESULTS_PER_FILE:
            continue
        if any(_overlaps(metadata, existing) for existing in selected):
            continue
        selected.append(metadata | {
            "score": round(item["rank_score"] / (1 + item["rank_score"]), 4),
            "vector_score": round(float(item.get("vector_score", 0)), 4),
            "lexical_score": round(float(item.get("lexical_score", 0)), 4),
            "retrieval": "hybrid" if len(item["sources"]) > 1 else next(iter(item["sources"])),
            "snippet": item["document"][:3600],
        })
        per_file[file_key] += 1
        if len(selected) >= limit:
            break
    return selected


def _overlaps(left: dict, right: dict) -> bool:
    if left.get("repo") != right.get("repo") or left.get("path") != right.get("path"):
        return False
    left_start, left_end = int(left.get("start_line", 0)), int(left.get("end_line", 0))
    right_start, right_end = int(right.get("start_line", 0)), int(right.get("end_line", 0))
    intersection = max(0, min(left_end, right_end) - max(left_start, right_start) + 1)
    shorter = max(1, min(left_end - left_start + 1, right_end - right_start + 1))
    return intersection / shorter >= 0.2
