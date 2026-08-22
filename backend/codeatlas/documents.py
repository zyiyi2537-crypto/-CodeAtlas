from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .models import DocumentChunkRecord
from .security import redact_secrets

_ALLOWED = {".md", ".markdown", ".txt", ".csv", ".docx", ".xlsx"}


def extract_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED:
        raise ValueError("supported document types are Markdown, TXT, CSV, DOCX and XLSX")
    if suffix == ".docx":
        return redact_secrets(_extract_docx(content))
    if suffix == ".xlsx":
        return redact_secrets(_extract_xlsx(content))
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("document must be UTF-8 text") from exc
    return redact_secrets(text)


def _extract_docx(content: bytes) -> str:
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(content)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise ValueError("invalid DOCX document") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
        if text.strip():
            paragraphs.append(text.strip())
    return "\n\n".join(paragraphs)


def _extract_xlsx(content: bytes) -> str:
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(content)) as archive:
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.text or "" for node in item.iter()) for item in root]
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            rel_map = {item.attrib["Id"]: item.attrib["Target"] for item in rels}
            rows = []
            for sheet in workbook.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"):
                rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                if not rel_id:
                    continue
                target = rel_map.get(rel_id, "")
                target = target.lstrip("/") if target.startswith("/") else "xl/" + target
                root = ElementTree.fromstring(archive.read(target))
                rows.append(f"## Sheet: {sheet.attrib.get('name', 'Sheet')}" )
                for row in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
                    values = []
                    for cell in row:
                        value = next(
                            iter(cell.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")),
                            None,
                        )
                        text = value.text if value is not None else ""
                        if cell.attrib.get("t") == "s" and text and text.isdigit():
                            text = shared[int(text)]
                        values.append(text or "")
                    if values:
                        rows.append(" | ".join(values))
            return "\n".join(rows)
    except (KeyError, ValueError, zipfile.BadZipFile, IndexError) as exc:
        raise ValueError("invalid XLSX document") from exc


def chunk_document(
    title: str, document_id: str, collection_id: str, text: str, max_chars: int = 3500
) -> list[DocumentChunkRecord]:
    sections: list[tuple[str, str]] = []
    heading = ""
    body: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+)$", line.strip())
        if match:
            if body:
                sections.append((heading, "\n".join(body).strip()))
            heading = match.group(1).strip()
            body = []
        else:
            body.append(line)
    if body:
        sections.append((heading, "\n".join(body).strip()))

    chunks: list[DocumentChunkRecord] = []
    for section, body_text in sections:
        if not body_text:
            continue
        for offset in range(0, len(body_text), max_chars):
            part = body_text[offset : offset + max_chars].strip()
            if not part:
                continue
            content = f"Document: {title}\nSection: {section}\n\n{part}"
            chunk_id = hashlib.sha256(
                f"{document_id}|{section}|{offset}|{part}".encode()
            ).hexdigest()
            chunks.append(
                DocumentChunkRecord(
                    id=chunk_id,
                    document_id=document_id,
                    collection_id=collection_id,
                    title=title,
                    section=section,
                    content=content,
                )
            )
    return chunks
