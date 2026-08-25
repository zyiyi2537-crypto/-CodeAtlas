from __future__ import annotations

from io import BytesIO

from sqlmodel import Session, select

from codeatlas.connectors import (
    MAX_EXTERNAL_ITEMS,
    ExternalItem,
    S3Connector,
    TencentCosConnector,
    _append_item,
)
from codeatlas.external_sync import ExternalSourceSyncService
from codeatlas.models import (
    Document,
    DocumentChunkRecord,
    DocumentCollection,
    ExternalSource,
    ExternalSourceItem,
)


class StreamingBody:
    def __init__(self, content: bytes):
        self.content = content
        self.offset = 0
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self.content) - self.offset
        chunk = self.content[self.offset : self.offset + amount]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeS3Client:
    def __init__(self):
        self.calls: list[dict] = []

    def list_objects_v2(self, **kwargs):
        self.calls.append(kwargs)
        if "ContinuationToken" not in kwargs:
            return {
                "Contents": [
                    {
                        "Key": "docs/guide.md",
                        "ETag": '"etag-1"',
                        "Size": 12,
                        "LastModified": "2026-08-25T00:00:00Z",
                    },
                    {"Key": "docs/image.png", "ETag": '"ignored"', "Size": 5},
                ],
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
            }
        return {
            "Contents": [
                {"Key": "docs/manual.pdf", "ETag": '"etag-2"', "Size": 42}
            ],
            "IsTruncated": False,
        }

    def get_object(self, **kwargs):
        assert kwargs == {"Bucket": "company-docs", "Key": "docs/guide.md"}
        return {"Body": StreamingBody(b"# Guide\n\nProduction workflow")}


def test_external_item_limit_is_enforced() -> None:
    items = [object()] * MAX_EXTERNAL_ITEMS
    try:
        _append_item(items, object())  # type: ignore[arg-type]
    except ValueError as exc:
        assert "20000" in str(exc)
    else:
        raise AssertionError("external item limit was not enforced")


def test_s3_uses_default_credential_chain_when_static_keys_are_absent(monkeypatch) -> None:
    captured: dict = {}

    def fake_client(service: str, **kwargs):
        captured.update({"service": service, **kwargs})
        return FakeS3Client()

    monkeypatch.setattr("boto3.client", fake_client)
    S3Connector(
        {"bucket": "company-docs", "region": "ap-southeast-1"},
        {},
    )

    assert captured["service"] == "s3"
    assert "aws_access_key_id" not in captured
    assert "aws_secret_access_key" not in captured


class FakeCosBody:
    def get_raw_stream(self):
        return BytesIO(b"# COS Guide\n\nDeployment notes")


class FakeCosClient:
    def __init__(self):
        self.calls: list[dict] = []

    def list_objects(self, **kwargs):
        self.calls.append(kwargs)
        if not kwargs.get("Marker"):
            return {
                "Contents": [
                    {"Key": "kb/guide.md", "ETag": '"cos-1"', "Size": "32"},
                ],
                "IsTruncated": "true",
                "NextMarker": "next-key",
            }
        return {
            "Contents": [
                {"Key": "kb/table.xlsx", "ETag": '"cos-2"', "Size": "64"},
            ],
            "IsTruncated": "false",
        }

    def get_object(self, **kwargs):
        assert kwargs == {"Bucket": "company-1250000000", "Key": "kb/guide.md"}
        return {"Body": FakeCosBody()}


def test_s3_connector_paginates_filters_supported_documents_and_downloads() -> None:
    client = FakeS3Client()
    connector = S3Connector(
        {
            "bucket": "company-docs",
            "prefix": "docs/",
            "region": "ap-southeast-1",
        },
        {"access_key_id": "test", "secret_access_key": "test"},
        client=client,
    )

    items = connector.list_items()

    assert [item.external_id for item in items] == ["docs/guide.md", "docs/manual.pdf"]
    assert [item.revision for item in items] == ["etag-1", "etag-2"]
    assert client.calls[1]["ContinuationToken"] == "page-2"
    assert connector.fetch(items[0]) == b"# Guide\n\nProduction workflow"


def test_cos_connector_paginates_and_downloads_supported_documents() -> None:
    client = FakeCosClient()
    connector = TencentCosConnector(
        {"bucket": "company-1250000000", "prefix": "kb/", "region": "ap-shanghai"},
        {"secret_id": "test", "secret_key": "test"},
        client=client,
    )

    items = connector.list_items()

    assert [item.external_id for item in items] == ["kb/guide.md", "kb/table.xlsx"]
    assert client.calls[1]["Marker"] == "next-key"
    assert connector.fetch(items[0]) == b"# COS Guide\n\nDeployment notes"


class MutableConnector:
    def __init__(self):
        self.items = [
            ExternalItem(
                external_id="docs/runbook.md",
                path="docs/runbook.md",
                title="Runbook",
                filename="runbook.md",
                mime_type="text/markdown",
                revision="v1",
                modified_at="2026-08-25T00:00:00Z",
                url="s3://company-docs/docs/runbook.md",
            )
        ]
        self.content = b"# Runbook\n\nRestart the worker safely."
        self.fetch_count = 0

    def test_connection(self) -> None:
        return None

    def list_items(self) -> list[ExternalItem]:
        return list(self.items)

    def fetch(self, _item: ExternalItem) -> bytes:
        self.fetch_count += 1
        return self.content


def _source(application, admin, name: str, provider: str = "aws_s3") -> str:
    with Session(application.state.engine) as session:
        collection = DocumentCollection(name=name, description="", created_by=admin.id)
        session.add(collection)
        session.flush()
        source = ExternalSource(
            name=f"{name} source",
            provider=provider,
            collection_id=collection.id,
            credential_ref="aws-docs",
            config_json='{"bucket":"company-docs","region":"ap-southeast-1"}',
            created_by=admin.id,
        )
        session.add(source)
        session.commit()
        return source.id


def test_external_sync_rejects_items_over_the_per_file_limit(
    application, admin, monkeypatch
) -> None:
    connector = MutableConnector()
    connector.content = b"x" * (20 * 1024 * 1024 + 1)
    source_id = _source(application, admin, "Limits")
    monkeypatch.setattr("codeatlas.external_sync.build_connector", lambda _source: connector)
    service = ExternalSourceSyncService(
        application.state.settings,
        application.state.engine,
        application.state.knowledge_search,
    )

    try:
        service.sync_source(source_id)
    except ValueError as exc:
        assert "20 MB" in str(exc)
    else:
        raise AssertionError("oversized external item was accepted")
    with Session(application.state.engine) as session:
        assert session.exec(select(Document)).all() == []


def test_external_chunks_preserve_connector_provenance(
    application, admin, monkeypatch
) -> None:
    connector = MutableConnector()
    source_id = _source(application, admin, "Provenance")
    monkeypatch.setattr("codeatlas.external_sync.build_connector", lambda _source: connector)
    service = ExternalSourceSyncService(
        application.state.settings,
        application.state.engine,
        application.state.knowledge_search,
    )

    service.sync_source(source_id)

    with Session(application.state.engine) as session:
        chunk = session.exec(select(DocumentChunkRecord)).one()
        metadata = __import__("json").loads(chunk.metadata_json)
    assert metadata["external_provider"] == "aws_s3"
    assert metadata["external_source_id"] == source_id
    assert metadata["external_id"] == "docs/runbook.md"
    assert metadata["source_url"] == "s3://company-docs/docs/runbook.md"


def test_external_sync_retries_same_revision_after_embedding_failure(
    application, admin, monkeypatch
) -> None:
    connector = MutableConnector()
    source_id = _source(application, admin, "Retry")
    monkeypatch.setattr("codeatlas.external_sync.build_connector", lambda _source: connector)
    original_index = application.state.knowledge_search.index_document
    attempts = 0

    def flaky_index(chunks):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("embedding unavailable")
        return original_index(chunks)

    monkeypatch.setattr(application.state.knowledge_search, "index_document", flaky_index)
    service = ExternalSourceSyncService(
        application.state.settings,
        application.state.engine,
        application.state.knowledge_search,
    )

    try:
        service.sync_source(source_id)
    except RuntimeError as exc:
        assert "embedding unavailable" in str(exc)
    else:
        raise AssertionError("embedding failure was swallowed")
    with Session(application.state.engine) as session:
        mapping = session.exec(select(ExternalSourceItem)).one()
        document = session.get(Document, mapping.document_id)
        assert mapping.revision == ""
        assert document is not None and document.status == "index_failed"

    retried = service.sync_source(source_id)
    assert retried.updated == 1
    assert connector.fetch_count == 2
    with Session(application.state.engine) as session:
        mapping = session.exec(select(ExternalSourceItem)).one()
        document = session.get(Document, mapping.document_id)
        assert mapping.revision == "v1"
        assert document is not None and document.status == "indexed"


def test_external_sync_records_connector_initialization_failure(
    application, admin, monkeypatch
) -> None:
    source_id = _source(application, admin, "Credential failure")
    monkeypatch.setattr(
        "codeatlas.external_sync.build_connector",
        lambda _source: (_ for _ in ()).throw(
            ValueError("token=secret-value-that-must-not-be-stored")
        ),
    )
    service = ExternalSourceSyncService(
        application.state.settings,
        application.state.engine,
        application.state.knowledge_search,
    )

    try:
        service.sync_source(source_id)
    except ValueError:
        pass
    else:
        raise AssertionError("connector initialization failure was swallowed")

    with Session(application.state.engine) as session:
        source = session.get(ExternalSource, source_id)
        assert source is not None and source.sync_status == "failed"
        assert "secret-value" not in source.last_error
        assert "[REDACTED]" in source.last_error


def test_remote_delete_keeps_mysql_truth_when_chroma_delete_fails(
    application, admin, monkeypatch
) -> None:
    connector = MutableConnector()
    source_id = _source(application, admin, "Delete retry")
    monkeypatch.setattr("codeatlas.external_sync.build_connector", lambda _source: connector)
    service = ExternalSourceSyncService(
        application.state.settings,
        application.state.engine,
        application.state.knowledge_search,
    )
    service.sync_source(source_id)
    connector.items = []
    monkeypatch.setattr(
        application.state.knowledge_search.vector_store,
        "delete_source",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("chroma unavailable")),
    )

    try:
        service.sync_source(source_id)
    except RuntimeError as exc:
        assert "chroma unavailable" in str(exc)
    else:
        raise AssertionError("Chroma deletion failure was swallowed")

    with Session(application.state.engine) as session:
        mapping = session.exec(select(ExternalSourceItem)).one()
        assert mapping.deleted_at is None
        assert mapping.document_id is not None
        assert session.get(Document, mapping.document_id) is not None
        assert len(session.exec(select(DocumentChunkRecord)).all()) == 1


def test_saas_search_disappearance_does_not_delete_documents(
    application, admin, monkeypatch
) -> None:
    connector = MutableConnector()
    source_id = _source(application, admin, "Notion permissions", provider="notion")
    monkeypatch.setattr("codeatlas.external_sync.build_connector", lambda _source: connector)
    service = ExternalSourceSyncService(
        application.state.settings,
        application.state.engine,
        application.state.knowledge_search,
    )
    service.sync_source(source_id)
    connector.items = []

    result = service.sync_source(source_id)

    assert result.deleted == 0
    with Session(application.state.engine) as session:
        mapping = session.exec(select(ExternalSourceItem)).one()
        assert mapping.deleted_at is None
        assert mapping.document_id is not None
        assert session.get(Document, mapping.document_id) is not None


def test_external_sync_is_incremental_and_propagates_remote_deletion(
    application, admin, monkeypatch
) -> None:
    connector = MutableConnector()
    with Session(application.state.engine) as session:
        collection = DocumentCollection(
            name="Cloud documents",
            description="",
            created_by=admin.id,
        )
        session.add(collection)
        session.flush()
        source = ExternalSource(
            name="AWS docs",
            provider="aws_s3",
            collection_id=collection.id,
            credential_ref="aws-docs",
            config_json='{"bucket":"company-docs","region":"ap-southeast-1"}',
            created_by=admin.id,
        )
        session.add(source)
        session.commit()
        source_id = source.id

    monkeypatch.setattr(
        "codeatlas.external_sync.build_connector",
        lambda _source: connector,
    )
    service = ExternalSourceSyncService(
        application.state.settings,
        application.state.engine,
        application.state.knowledge_search,
    )

    first = service.sync_source(source_id)
    assert first.created == 1
    assert first.updated == 0
    assert first.deleted == 0
    assert connector.fetch_count == 1
    with Session(application.state.engine) as session:
        first_document = session.exec(select(Document)).one()
        first_source_path = first_document.source_path
    assert __import__("pathlib").Path(first_source_path).is_file()

    second = service.sync_source(source_id)
    assert second.unchanged == 1
    assert connector.fetch_count == 1

    connector.items[0] = ExternalItem(
        **{**connector.items[0].__dict__, "revision": "v2"}
    )
    connector.content = b"# Runbook\n\nRestart the worker and verify health."
    third = service.sync_source(source_id)
    assert third.updated == 1
    assert connector.fetch_count == 2

    with Session(application.state.engine) as session:
        mapping = session.exec(
            select(ExternalSourceItem).where(ExternalSourceItem.source_id == source_id)
        ).one()
        document = session.get(Document, mapping.document_id)
        assert document is not None and document.version == 2
        second_source_path = document.source_path
        assert len(
            session.exec(
                select(DocumentChunkRecord).where(
                    DocumentChunkRecord.document_id == document.id
                )
            ).all()
        ) == 1
    assert second_source_path != first_source_path
    assert __import__("pathlib").Path(second_source_path).is_file()
    assert not __import__("pathlib").Path(first_source_path).exists()

    connector.items = []
    deleted = service.sync_source(source_id)
    assert deleted.deleted == 1
    with Session(application.state.engine) as session:
        mapping = session.exec(
            select(ExternalSourceItem).where(ExternalSourceItem.source_id == source_id)
        ).one()
        assert mapping.document_id is None
        assert mapping.deleted_at is not None
        assert session.exec(select(Document)).all() == []
        assert session.exec(select(DocumentChunkRecord)).all() == []
