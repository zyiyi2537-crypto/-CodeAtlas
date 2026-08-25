from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import SimpleNamespace

from sqlmodel import Session, select

from .connectors import MAX_EXTERNAL_ITEMS, ExternalItem, build_connector
from .documents import chunk_document, extract_structured_blocks
from .models import (
    Document,
    DocumentChunkRecord,
    ExternalSource,
    ExternalSourceItem,
    utc_now,
)
from .security import redact_secrets
from .settings import Settings

MAX_EXTERNAL_FILE_BYTES = 20 * 1024 * 1024
MAX_EXTERNAL_SYNC_BYTES = 100 * 1024 * 1024
AUTHORITATIVE_INVENTORY_PROVIDERS = {"aws_s3", "tencent_cos"}
logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0


class ExternalSourceSyncService:
    def __init__(self, settings: Settings, engine, knowledge_search):
        self.settings = settings
        self.engine = engine
        self.knowledge_search = knowledge_search
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="codeatlas-external-source"
        )
        self.lock = threading.Lock()
        self.running_sources: set[str] = set()
        self.embedding_switch_in_progress = False

    def _begin_source_operation(self, source_id: str) -> None:
        with self.lock:
            if self.embedding_switch_in_progress:
                raise RuntimeError("Embedding profile switch is in progress")
            if source_id in self.running_sources:
                raise RuntimeError("External source synchronization is already running")
            self.running_sources.add(source_id)

    def _end_source_operation(self, source_id: str) -> None:
        with self.lock:
            self.running_sources.discard(source_id)

    def submit(self, source_id: str) -> None:
        self._begin_source_operation(source_id)
        try:
            with Session(self.engine) as session:
                source = session.get(ExternalSource, source_id)
                if not source:
                    raise LookupError(source_id)
                source.sync_status = "queued"
                source.last_error = ""
                session.add(source)
                session.commit()
            self.executor.submit(self._run_submitted, source_id)
        except Exception:
            self._end_source_operation(source_id)
            raise

    def _run_submitted(self, source_id: str) -> None:
        try:
            self._sync_source(source_id)
        except Exception:
            # Details are already redacted into ExternalSource.last_error. Keep
            # exception text and provider request data out of process logs.
            logger.error("External source synchronization failed: %s", source_id)
        finally:
            self._end_source_operation(source_id)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def begin_embedding_switch(self) -> None:
        with self.lock:
            if self.embedding_switch_in_progress or self.running_sources:
                raise RuntimeError("External source synchronization is running")
            self.embedding_switch_in_progress = True

    def end_embedding_switch(self) -> None:
        with self.lock:
            self.embedding_switch_in_progress = False

    def test_source(self, source_id: str) -> None:
        with Session(self.engine) as session:
            source = session.get(ExternalSource, source_id)
            if not source:
                raise LookupError(source_id)
            source_snapshot = SimpleNamespace(
                provider=source.provider,
                credential_ref=source.credential_ref,
                config_json=source.config_json,
            )
        connector = build_connector(source_snapshot)
        try:
            connector.test_connection()
        finally:
            close = getattr(connector, "close", None)
            if callable(close):
                close()

    def sync_source(self, source_id: str) -> SyncResult:
        self._begin_source_operation(source_id)
        try:
            return self._sync_source(source_id)
        finally:
            self._end_source_operation(source_id)

    def _sync_source(self, source_id: str) -> SyncResult:
        with Session(self.engine) as session:
            source = session.get(ExternalSource, source_id)
            if not source:
                raise LookupError(source_id)
            source.sync_status = "syncing"
            source.last_error = ""
            source.last_checked_at = utc_now()
            session.add(source)
            session.commit()
            source_data = {
                "id": source.id,
                "collection_id": source.collection_id,
                "created_by": source.created_by,
                "provider": source.provider,
                "credential_ref": source.credential_ref,
                "config_json": source.config_json,
            }
        result = SyncResult()
        downloaded_bytes = 0
        connector = None
        try:
            connector = build_connector(SimpleNamespace(**source_data))
            listed_items = connector.list_items()
            if len(listed_items) > MAX_EXTERNAL_ITEMS:
                raise ValueError(
                    f"External source exceeds the {MAX_EXTERNAL_ITEMS} item limit"
                )
            remote_items = {item.external_id: item for item in listed_items}
            if len(remote_items) != len(listed_items):
                raise ValueError("External source returned duplicate item identifiers")
            with Session(self.engine) as session:
                mappings = {
                    item.external_id: item
                    for item in session.exec(
                        select(ExternalSourceItem).where(
                            ExternalSourceItem.source_id == source_id
                        )
                    ).all()
                }
            for external_id, item in remote_items.items():
                mapping = mappings.get(external_id)
                if (
                    mapping
                    and mapping.deleted_at is None
                    and mapping.document_id
                    and mapping.revision == item.revision
                ):
                    result.unchanged += 1
                    continue
                if item.size > MAX_EXTERNAL_FILE_BYTES:
                    raise ValueError(
                        f"External item exceeds the 20 MB limit: {item.path}"
                    )
                content = connector.fetch(item)
                if len(content) > MAX_EXTERNAL_FILE_BYTES:
                    raise ValueError(
                        f"External item exceeds the 20 MB limit: {item.path}"
                    )
                downloaded_bytes += len(content)
                if downloaded_bytes > MAX_EXTERNAL_SYNC_BYTES:
                    raise ValueError("External synchronization exceeds the 100 MB run limit")
                self._upsert_item(source_data, mapping, item, content)
                if mapping and mapping.document_id:
                    result.updated += 1
                else:
                    result.created += 1
            if source_data["provider"] in AUTHORITATIVE_INVENTORY_PROVIDERS:
                for external_id, mapping in mappings.items():
                    if external_id not in remote_items and mapping.deleted_at is None:
                        self._delete_item(mapping.id)
                        result.deleted += 1
            with Session(self.engine) as session:
                stored = session.get(ExternalSource, source_id)
                if stored:
                    stored.sync_status = "idle"
                    stored.last_error = ""
                    stored.last_checked_at = utc_now()
                    stored.last_result_json = json.dumps(asdict(result))
                    session.add(stored)
                    session.commit()
            return result
        except Exception as exc:
            with Session(self.engine) as session:
                stored = session.get(ExternalSource, source_id)
                if stored:
                    stored.sync_status = "failed"
                    stored.last_error = redact_secrets(str(exc))[:2000]
                    stored.last_checked_at = utc_now()
                    session.add(stored)
                    session.commit()
            raise
        finally:
            close = getattr(connector, "close", None)
            if callable(close):
                close()

    def _upsert_item(
        self,
        source: dict[str, str],
        mapping: ExternalSourceItem | None,
        item: ExternalItem,
        content: bytes,
    ) -> None:
        blocks = extract_structured_blocks(item.filename, content)
        provenance = {
            "external_provider": source["provider"],
            "external_source_id": source["id"],
            "external_id": item.external_id,
            "external_path": item.path,
            "source_url": item.url,
            "external_revision": item.revision,
        }
        blocks = [
            replace(
                block,
                metadata={**block.metadata, **provenance},
            )
            for block in blocks
        ]
        with Session(self.engine) as session:
            existing_mapping = (
                session.get(ExternalSourceItem, mapping.id) if mapping else None
            )
            document = (
                session.get(Document, existing_mapping.document_id)
                if existing_mapping and existing_mapping.document_id
                else None
            )
            old_document_id = document.id if document else ""
            if document:
                old_chunks = session.exec(
                    select(DocumentChunkRecord).where(
                        DocumentChunkRecord.document_id == document.id
                    )
                ).all()
                for chunk in old_chunks:
                    session.delete(chunk)
                document.version += 1
            else:
                document = Document(
                    collection_id=source["collection_id"],
                    title=item.title[:300],
                    original_filename=item.filename[:500],
                    mime_type=item.mime_type[:120],
                    status="indexing",
                    version=1,
                    source_path="",
                    sha256="",
                    created_by=source["created_by"],
                )
            document.title = item.title[:300]
            document.original_filename = item.filename[:500]
            document.mime_type = item.mime_type[:120]
            document.sha256 = hashlib.sha256(content).hexdigest()
            document.status = (
                "ocr_required"
                if blocks and all(block.kind == "ocr-required" for block in blocks)
                else "indexing"
            )
            safe_filename = item.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if not safe_filename or safe_filename in {".", ".."} or "\x00" in safe_filename:
                raise ValueError("External item has an unsafe filename")
            raw_dir = self.settings.data_dir / "documents" / document.id
            raw_dir.mkdir(parents=True, exist_ok=True)
            suffix = Path(safe_filename).suffix.lower()
            storage_name = (
                f"v{document.version}-"
                f"{hashlib.sha256(item.external_id.encode()).hexdigest()[:16]}{suffix}"
            )
            raw_path = raw_dir / storage_name
            temporary_raw_path = raw_dir / f".{storage_name}.syncing"
            temporary_raw_path.write_bytes(content)
            os.replace(temporary_raw_path, raw_path)
            document.source_path = str(raw_path)
            chunks = chunk_document(
                document.title,
                document.id,
                source["collection_id"],
                blocks=blocks,
            )
            session.add(document)
            session.add_all(chunks)
            if existing_mapping is None:
                existing_mapping = ExternalSourceItem(
                    source_id=source["id"],
                    external_id=item.external_id,
                    external_id_hash=hashlib.sha256(item.external_id.encode()).hexdigest(),
                    path=item.path,
                    title=item.title,
                )
            existing_mapping.document_id = document.id
            existing_mapping.path = item.path
            existing_mapping.title = item.title[:300]
            existing_mapping.mime_type = item.mime_type[:120]
            existing_mapping.modified_at = item.modified_at[:100]
            existing_mapping.source_url = item.url[:2000]
            existing_mapping.last_synced_at = utc_now()
            existing_mapping.deleted_at = None
            session.add(existing_mapping)
            session.commit()
            # SQLAlchemy expires ORM attributes on commit. Keep the Session open
            # while KnowledgeSearch reads the committed chunk values for Chroma.
            if old_document_id:
                self.knowledge_search.vector_store.delete_source("document", old_document_id)
            try:
                if chunks:
                    self.knowledge_search.index_document(chunks)
            except Exception:
                document.status = "index_failed"
                existing_mapping.revision = ""
                session.add(document)
                session.add(existing_mapping)
                session.commit()
                raise
            existing_mapping.revision = item.revision[:500]
            document.status = (
                "ocr_required"
                if blocks and all(block.kind == "ocr-required" for block in blocks)
                else "indexed"
            )
            session.add(document)
            session.add(existing_mapping)
            session.commit()
        for stale_path in raw_dir.iterdir():
            if stale_path != raw_path and stale_path.is_file():
                stale_path.unlink(missing_ok=True)

    def _delete_item(self, mapping_id: str) -> None:
        with Session(self.engine) as session:
            mapping = session.get(ExternalSourceItem, mapping_id)
            if not mapping or mapping.deleted_at is not None:
                return
            document_id = mapping.document_id or ""
            document_path = ""
            if document_id:
                document = session.get(Document, document_id)
                document_path = document.source_path if document else ""
            # Chroma is a derived projection, but deleting it first is idempotent.
            # If it fails, keep MySQL truth intact so the whole deletion can retry.
            if document_id:
                self.knowledge_search.vector_store.delete_source("document", document_id)
            document = session.get(Document, document_id) if document_id else None
            if document:
                for chunk in session.exec(
                    select(DocumentChunkRecord).where(
                        DocumentChunkRecord.document_id == document.id
                    )
                ).all():
                    session.delete(chunk)
                # No ORM relationship is declared between these models, so force
                # child deletion before the parent to satisfy MySQL's foreign key.
                session.flush()
                session.delete(document)
            mapping.document_id = None
            mapping.deleted_at = utc_now()
            mapping.last_synced_at = utc_now()
            session.add(mapping)
            session.commit()
        if document_path:
            shutil.rmtree(Path(document_path).parent, ignore_errors=True)

    def delete_source(self, source_id: str) -> None:
        with self.lock:
            if self.embedding_switch_in_progress:
                raise RuntimeError("Embedding profile switch is in progress")
            if source_id in self.running_sources:
                raise RuntimeError("External source synchronization is running")
            self.running_sources.add(source_id)
        try:
            with Session(self.engine) as session:
                source = session.get(ExternalSource, source_id)
                if not source:
                    raise LookupError(source_id)
                mapping_ids = [
                    item.id
                    for item in session.exec(
                        select(ExternalSourceItem).where(
                            ExternalSourceItem.source_id == source_id
                        )
                    ).all()
                ]
            for mapping_id in mapping_ids:
                self._delete_item(mapping_id)
            with Session(self.engine) as session:
                source = session.get(ExternalSource, source_id)
                if source:
                    for mapping in session.exec(
                        select(ExternalSourceItem).where(
                            ExternalSourceItem.source_id == source_id
                        )
                    ).all():
                        session.delete(mapping)
                    session.flush()
                    session.delete(source)
                    session.commit()
        finally:
            with self.lock:
                self.running_sources.discard(source_id)
