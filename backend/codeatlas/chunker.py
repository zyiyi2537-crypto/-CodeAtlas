from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tree_sitter import Language, Parser

from .security import redact_secrets

LANGUAGES = {
    ".java": "java", ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".html": "html", ".vue": "vue",
    ".sql": "sql", ".md": "markdown", ".yml": "yaml", ".yaml": "yaml",
    ".json": "json", ".xml": "xml", ".properties": "properties",
}
PARSED_LANGUAGES = {"java", "python", "javascript", "typescript"}
ACCEPTED_NODES = {
    "java": {
        "method_declaration",
        "constructor_declaration",
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
    },
    "python": {"function_definition", "class_definition"},
    "javascript": {
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
        "class_declaration",
    },
    "typescript": {
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
    },
}


@dataclass(frozen=True)
class CodeChunk:
    id: str
    repository_id: str
    generation_id: str
    commit: str
    path: str
    language: str
    symbol: str
    start_line: int
    end_line: int
    content: str


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", raw, 0, 1, f"unsupported text encoding: {path}")


@lru_cache(maxsize=len(PARSED_LANGUAGES))
def grammar_for(language: str) -> Language:
    if language == "java":
        import tree_sitter_java as java_grammar

        return Language(java_grammar.language())
    elif language == "python":
        import tree_sitter_python as python_grammar

        return Language(python_grammar.language())
    elif language == "javascript":
        import tree_sitter_javascript as javascript_grammar

        return Language(javascript_grammar.language())
    elif language == "typescript":
        import tree_sitter_typescript as typescript_grammar

        return Language(typescript_grammar.language_typescript())
    raise ValueError(language)


def parser_for(language: str) -> Parser:
    grammar = grammar_for(language)
    try:
        return Parser(grammar)
    except TypeError:
        parser = Parser()
        parser.language = grammar
        return parser


def walk(node, accepted: set[str]) -> Iterable:
    if node.type in accepted:
        yield node
    for child in node.children:
        yield from walk(child, accepted)


def line_windows(lines: list[str], start_line: int, end_line: int, max_chars: int = 3200):
    cursor = start_line - 1
    while cursor < end_line:
        size = 0
        finish = cursor
        while finish < end_line and finish - cursor < 80:
            next_size = len(lines[finish]) + 1
            if finish > cursor and size + next_size > max_chars:
                break
            size += next_size
            finish += 1
        finish = max(finish, cursor + 1)
        yield cursor + 1, finish, "\n".join(lines[cursor:finish])
        if finish >= end_line:
            break
        cursor = max(cursor + 1, finish - min(10, (finish - cursor) // 5))


def chunk_ranges(text: str, language: str) -> list[tuple[str, int, int]]:
    lines = text.splitlines()
    if language not in PARSED_LANGUAGES:
        return [("file", 1, len(lines))]
    encoded = text.encode("utf-8")
    parser = parser_for(language)
    tree = parser.parse(encoded)
    root = tree.root_node
    ranges = [("file-overview", 1, min(len(lines), 80))]
    for node in walk(root, ACCEPTED_NODES[language]):
        name = node.child_by_field_name("name")
        symbol = (
            encoded[name.start_byte:name.end_byte].decode("utf-8", errors="replace")
            if name is not None else node.type
        )
        ranges.append((symbol, node.start_point.row + 1, node.end_point.row + 1))
    return sorted(set(ranges), key=lambda item: (item[1], item[2], item[0]))


def chunk_file(
    path: Path, root: Path, repository_id: str, generation_id: str, commit: str
) -> list[CodeChunk]:
    language = LANGUAGES[path.suffix.lower()]
    text = redact_secrets(read_text(path))
    lines = text.splitlines()
    if not lines:
        return []
    relative = path.relative_to(root).as_posix()
    chunks: list[CodeChunk] = []
    seen: set[tuple[int, int, str]] = set()
    for symbol, range_start, range_end in chunk_ranges(text, language):
        range_start = max(1, min(range_start, len(lines)))
        range_end = max(range_start, min(range_end, len(lines)))
        for part, (start, end, body) in enumerate(
            line_windows(lines, range_start, range_end), start=1
        ):
            normalized = body.strip()
            if len(normalized) < 20:
                continue
            part_symbol = symbol if part == 1 else f"{symbol}#part-{part}"
            identity = (start, end, part_symbol)
            if identity in seen:
                continue
            seen.add(identity)
            content = (
                f"Repository: {repository_id}\nFile: {relative}\nLanguage: {language}\n"
                f"Symbol: {part_symbol}\nLines: {start}-{end}\n\n{normalized}"
            )
            chunk_id = hashlib.sha256(
                f"{repository_id}|{generation_id}|{relative}|{start}|{end}|{part_symbol}".encode()
            ).hexdigest()
            chunks.append(CodeChunk(
                id=chunk_id, repository_id=repository_id, generation_id=generation_id,
                commit=commit, path=relative, language=language, symbol=part_symbol,
                start_line=start, end_line=end, content=content,
            ))
    return chunks
