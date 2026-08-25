from __future__ import annotations

import ipaddress
import json
import mimetypes
import os
import socket
import threading
from dataclasses import dataclass
from ipaddress import IPv6Address, IPv6Network
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

SUPPORTED_DOCUMENT_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".csv",
    ".docx",
    ".xlsx",
    ".pdf",
    ".pptx",
}
MAX_EXTERNAL_ITEMS = 20_000


@dataclass(frozen=True)
class ExternalItem:
    external_id: str
    path: str
    title: str
    filename: str
    mime_type: str
    revision: str
    modified_at: str = ""
    url: str = ""
    size: int = 0


@dataclass(frozen=True)
class ResolvedEndpoint:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


_IPV6_TRANSITION_NETWORKS = (
    IPv6Network("64:ff9b::/96"),
    IPv6Network("64:ff9b:1::/48"),
)
_ALLOWLISTED_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


def _is_public_destination(address: str) -> bool:
    value = ipaddress.ip_address(address.split("%", 1)[0])
    if not value.is_global:
        return False
    if isinstance(value, IPv6Address):
        if value.ipv4_mapped is not None or value.sixtofour is not None or value.teredo is not None:
            return False
        if any(value in network for network in _IPV6_TRANSITION_NETWORKS):
            return False
    return True


def _is_allowlisted_private_destination(address: str) -> bool:
    value = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(value, IPv6Address) and (
        value.ipv4_mapped is not None
        or value.sixtofour is not None
        or value.teredo is not None
        or any(value in network for network in _IPV6_TRANSITION_NETWORKS)
    ):
        return False
    return any(value in network for network in _ALLOWLISTED_PRIVATE_NETWORKS)


def resolve_public_endpoint(
    value: str,
    *,
    skip_network: bool = False,
    allow_private_host: bool = False,
) -> ResolvedEndpoint:
    from urllib.parse import urlsplit

    parsed = urlsplit(value.strip().rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("External base URL must be public HTTPS without credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("External base URL must not contain query parameters or fragments")
    try:
        parsed_port = parsed.port
        port = 443 if parsed_port is None else parsed_port
    except ValueError as exc:
        raise ValueError("External base URL has an invalid port") from exc
    if not 1 <= port <= 65535:
        raise ValueError("External base URL has an invalid port")
    hostname = parsed.hostname.lower().rstrip(".")
    if skip_network:
        return ResolvedEndpoint(parsed.geturl().rstrip("/"), hostname, port, ())
    try:
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"External host DNS resolution failed: {exc}") from exc
    addresses = tuple(dict.fromkeys(str(answer[4][0]) for answer in answers))
    if not addresses:
        raise ValueError("External host DNS resolution returned no addresses")
    allowed_private_hosts = {
        item.strip().lower().rstrip(".")
        for item in os.getenv("CODEATLAS_ALLOWED_EXTERNAL_HOSTS", "").split(",")
        if item.strip()
    }
    private_host_allowed = allow_private_host and hostname in allowed_private_hosts
    for address in addresses:
        if _is_public_destination(address):
            continue
        if private_host_allowed and _is_allowlisted_private_destination(address):
            continue
        raise ValueError("External base URL resolves to a non-public address")
    return ResolvedEndpoint(parsed.geturl().rstrip("/"), hostname, port, addresses)


def build_pinned_httpx_transport(endpoint: ResolvedEndpoint) -> httpx.HTTPTransport:
    from httpcore._backends.sync import SyncBackend

    class PinnedNetworkBackend(SyncBackend):
        def __init__(self) -> None:
            self._backend = SyncBackend()
            self._lock = threading.Lock()
            self._next_address = 0

        def connect_tcp(self, host, port, **kwargs):
            normalized = host.decode() if isinstance(host, bytes) else str(host)
            if normalized.lower().rstrip(".") != endpoint.hostname:
                raise OSError("Outbound connection host does not match the validated endpoint")
            with self._lock:
                start = self._next_address
                self._next_address = (self._next_address + 1) % len(endpoint.addresses)
            last_error: Exception | None = None
            for offset in range(len(endpoint.addresses)):
                address = endpoint.addresses[(start + offset) % len(endpoint.addresses)]
                try:
                    return self._backend.connect_tcp(address, port, **kwargs)
                except Exception as exc:
                    last_error = exc
            assert last_error is not None
            raise last_error

        def connect_unix_socket(self, path, **kwargs):
            return self._backend.connect_unix_socket(path, **kwargs)

        def sleep(self, seconds):
            return self._backend.sleep(seconds)

    if not endpoint.addresses:
        raise ValueError("Pinned HTTP transport requires validated endpoint addresses")
    transport = httpx.HTTPTransport(trust_env=False)
    transport._pool._network_backend = PinnedNetworkBackend()
    return transport


def build_pinned_s3_session(
    hostname: str, addresses: tuple[str, ...], *, port: int = 443
):
    from botocore.awsrequest import AWSHTTPSConnection, AWSHTTPSConnectionPool
    from botocore.httpsession import URLLib3Session
    from urllib3.util import connection as urllib3_connection

    if not addresses:
        raise ValueError("Pinned S3 session requires validated endpoint addresses")
    expected_hostname = hostname.lower().rstrip(".")

    class PinnedURLLib3Session(URLLib3Session):
        def send(self, request):
            from urllib.parse import urlsplit

            parsed = urlsplit(request.url)
            try:
                request_port = parsed.port
            except ValueError as exc:
                raise ValueError("S3 request escaped the validated endpoint") from exc
            request_port = 443 if request_port is None else request_port
            request_hostname = (parsed.hostname or "").lower().rstrip(".")
            if (
                parsed.scheme != "https"
                or request_hostname != expected_hostname
                or request_port != port
                or parsed.username
                or parsed.password
            ):
                raise ValueError("S3 request escaped the validated endpoint")
            return super().send(request)

    class PinnedAWSHTTPSConnection(AWSHTTPSConnection):
        def _new_conn(self):
            if self.host.lower().rstrip(".") != expected_hostname:
                raise OSError("S3 connection host does not match the validated endpoint")
            last_error: Exception | None = None
            for address in addresses:
                try:
                    return urllib3_connection.create_connection(
                        (address, self.port),
                        self.timeout,
                        source_address=self.source_address,
                        socket_options=self.socket_options,
                    )
                except OSError as exc:
                    last_error = exc
            assert last_error is not None
            raise last_error

    class PinnedAWSHTTPSConnectionPool(AWSHTTPSConnectionPool):
        ConnectionCls = PinnedAWSHTTPSConnection

    session = PinnedURLLib3Session(proxies={})
    session._pool_classes_by_scheme["https"] = PinnedAWSHTTPSConnectionPool
    session._manager.pool_classes_by_scheme["https"] = PinnedAWSHTTPSConnectionPool
    return session


class Connector(Protocol):
    def test_connection(self) -> None: ...
    def list_items(self) -> list[ExternalItem]: ...
    def fetch(self, item: ExternalItem) -> bytes: ...


def _append_item(items: list[ExternalItem], item: ExternalItem) -> None:
    if len(items) >= MAX_EXTERNAL_ITEMS:
        raise ValueError(f"External source exceeds the {MAX_EXTERNAL_ITEMS} item limit")
    items.append(item)


def _supported(key: str) -> bool:
    return Path(key).suffix.lower() in SUPPORTED_DOCUMENT_SUFFIXES


def _mime_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def credential_environment_name(reference: str) -> str:
    return "CODEATLAS_CREDENTIAL_" + reference.upper().replace("-", "_").replace(".", "_")


def _read_limited(stream, limit: int = 20 * 1024 * 1024) -> bytes:
    content = bytearray()
    while True:
        chunk = stream.read(min(1024 * 1024, limit + 1 - len(content)))
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > limit:
            raise ValueError("External item exceeds the 20 MB limit")


def resolve_connector_credential(reference: str) -> dict[str, str]:
    value = os.getenv(credential_environment_name(reference), "")
    if not value:
        raise ValueError(
            "External source credential is not configured on the server: "
            f"{credential_environment_name(reference)}"
        )
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("External source credential must be a JSON object") from exc
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in payload.items()
    ):
        raise ValueError("External source credential must contain string fields")
    return payload


class S3Connector:
    def __init__(
        self,
        config: dict[str, Any],
        credential: dict[str, str],
        *,
        client=None,
    ) -> None:
        self.bucket = str(config.get("bucket", "")).strip()
        self.prefix = str(config.get("prefix", "")).strip().lstrip("/")
        self.region = str(config.get("region", "")).strip()
        self.endpoint_url = str(config.get("endpoint_url", "")).strip() or None
        if not self.bucket or not self.region:
            raise ValueError("AWS S3 requires bucket and region")
        resolved_endpoint = None
        if self.endpoint_url:
            resolved_endpoint = resolve_public_endpoint(self.endpoint_url)
            self.endpoint_url = resolved_endpoint.url
        if client is None:
            import boto3

            options: dict[str, Any] = {
                "region_name": self.region,
                "endpoint_url": self.endpoint_url,
            }
            if credential.get("access_key_id") and credential.get("secret_access_key"):
                options.update(
                    aws_access_key_id=credential["access_key_id"],
                    aws_secret_access_key=credential["secret_access_key"],
                    aws_session_token=credential.get("session_token") or None,
                )
            client = boto3.client("s3", **options)
            if resolved_endpoint is not None:
                pinned_session = build_pinned_s3_session(
                    resolved_endpoint.hostname,
                    resolved_endpoint.addresses,
                    port=resolved_endpoint.port,
                )
                old_session = client._endpoint.http_session
                client._endpoint.http_session = pinned_session
                old_session.close()
        self.client = client

    def test_connection(self) -> None:
        self.client.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix, MaxKeys=1)

    def list_items(self) -> list[ExternalItem]:
        items: list[ExternalItem] = []
        token = ""
        while True:
            request: dict[str, Any] = {"Bucket": self.bucket, "Prefix": self.prefix}
            if token:
                request["ContinuationToken"] = token
            response = self.client.list_objects_v2(**request)
            for entry in response.get("Contents", []):
                key = str(entry.get("Key", ""))
                if not key or not _supported(key):
                    continue
                revision = str(entry.get("ETag", "")).strip('"')
                modified = entry.get("LastModified", "")
                modified_at = (
                    modified.isoformat() if hasattr(modified, "isoformat") else str(modified)
                )
                _append_item(
                    items,
                    ExternalItem(
                        external_id=key,
                        path=key,
                        title=Path(key).stem,
                        filename=Path(key).name,
                        mime_type=_mime_type(key),
                        revision=revision or f"size:{entry.get('Size', 0)}:{modified_at}",
                        modified_at=modified_at,
                        url=f"s3://{self.bucket}/{key}",
                        size=int(entry.get("Size", 0)),
                    )
                )
            if not response.get("IsTruncated"):
                break
            token = str(response.get("NextContinuationToken", ""))
            if not token:
                raise ValueError("AWS S3 pagination returned no continuation token")
        return items

    def fetch(self, item: ExternalItem) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=item.external_id)["Body"]
        try:
            return _read_limited(body)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


class TencentCosConnector:
    def __init__(
        self,
        config: dict[str, Any],
        credential: dict[str, str],
        *,
        client=None,
    ) -> None:
        self.bucket = str(config.get("bucket", "")).strip()
        self.prefix = str(config.get("prefix", "")).strip().lstrip("/")
        self.region = str(config.get("region", "")).strip()
        if not self.bucket or not self.region:
            raise ValueError("Tencent COS requires bucket and region")
        if client is None:
            from qcloud_cos import CosConfig, CosS3Client

            if not credential.get("secret_id") or not credential.get("secret_key"):
                raise ValueError("Tencent COS credential requires secret_id and secret_key")

            cos_config = CosConfig(
                Region=self.region,
                SecretId=credential.get("secret_id", ""),
                SecretKey=credential.get("secret_key", ""),
                Token=credential.get("token") or None,
            )
            client = CosS3Client(cos_config)
        self.client = client

    def test_connection(self) -> None:
        self.client.list_objects(Bucket=self.bucket, Prefix=self.prefix, MaxKeys=1)

    def list_items(self) -> list[ExternalItem]:
        items: list[ExternalItem] = []
        marker = ""
        while True:
            request: dict[str, Any] = {"Bucket": self.bucket, "Prefix": self.prefix}
            if marker:
                request["Marker"] = marker
            response = self.client.list_objects(**request)
            for entry in response.get("Contents", []):
                key = str(entry.get("Key", ""))
                if not key or not _supported(key):
                    continue
                modified_at = str(entry.get("LastModified", ""))
                revision = str(entry.get("ETag", "")).strip('"')
                _append_item(
                    items,
                    ExternalItem(
                        external_id=key,
                        path=key,
                        title=Path(key).stem,
                        filename=Path(key).name,
                        mime_type=_mime_type(key),
                        revision=revision or f"size:{entry.get('Size', 0)}:{modified_at}",
                        modified_at=modified_at,
                        url=(
                            f"https://{self.bucket}.cos.{self.region}.myqcloud.com/"
                            f"{quote(key)}"
                        ),
                        size=int(entry.get("Size", 0)),
                    )
                )
            if str(response.get("IsTruncated", "false")).lower() != "true":
                break
            marker = str(response.get("NextMarker", ""))
            if not marker:
                raise ValueError("Tencent COS pagination returned no next marker")
        return items

    def fetch(self, item: ExternalItem) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=item.external_id)["Body"]
        stream = body.get_raw_stream() if hasattr(body, "get_raw_stream") else body
        try:
            return _read_limited(stream)
        finally:
            close_stream = getattr(stream, "close", None)
            if callable(close_stream):
                close_stream()
            if stream is not body:
                close_body = getattr(body, "close", None)
                if callable(close_body):
                    close_body()

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


def _rich_text(block: dict[str, Any]) -> str:
    value = block.get(block.get("type", ""), {})
    return "".join(
        str(part.get("plain_text", ""))
        for part in value.get("rich_text", [])
        if isinstance(part, dict)
    )


class NotionConnector:
    def __init__(
        self,
        config: dict[str, Any],
        credential: dict[str, str],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        token = credential.get("token", "").strip()
        if not token:
            raise ValueError("Notion credential requires token")
        self.root_page_id = str(config.get("root_page_id", "")).strip()
        self.client = httpx.Client(
            base_url="https://api.notion.com/v1",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2025-09-03",
                "Content-Type": "application/json",
            },
            timeout=30,
            transport=transport,
        )

    def test_connection(self) -> None:
        response = self.client.post("/search", json={"page_size": 1})
        response.raise_for_status()

    @staticmethod
    def _title(page: dict[str, Any]) -> str:
        for prop in page.get("properties", {}).values():
            if isinstance(prop, dict) and isinstance(prop.get("title"), list):
                title = "".join(str(item.get("plain_text", "")) for item in prop["title"])
                if title:
                    return title
        return "Untitled"

    def list_items(self) -> list[ExternalItem]:
        if self.root_page_id:
            return self._root_items()
        items: list[ExternalItem] = []
        cursor = ""
        while True:
            payload: dict[str, Any] = {
                "page_size": 100,
                "filter": {"property": "object", "value": "page"},
            }
            if cursor:
                payload["start_cursor"] = cursor
            response = self.client.post("/search", json=payload)
            response.raise_for_status()
            data = response.json()
            for page in data.get("results", []):
                page_id = str(page.get("id", ""))
                if not page_id or page.get("archived") or page.get("in_trash"):
                    continue
                _append_item(items, self._page_item(page))
            if not data.get("has_more"):
                break
            cursor = str(data.get("next_cursor", ""))
            if not cursor:
                raise ValueError("Notion pagination returned no cursor")
        return items

    def _page_item(self, page: dict[str, Any]) -> ExternalItem:
        page_id = str(page.get("id", ""))
        edited = str(page.get("last_edited_time", ""))
        return ExternalItem(
            external_id=page_id,
            path=f"notion/{page_id}.md",
            title=self._title(page),
            filename=f"{page_id}.md",
            mime_type="text/markdown",
            revision=edited,
            modified_at=edited,
            url=str(page.get("url", "")),
        )

    def _root_items(self) -> list[ExternalItem]:
        pending = [self.root_page_id]
        seen: set[str] = set()
        items: list[ExternalItem] = []
        while pending:
            page_id = pending.pop(0)
            if page_id in seen:
                continue
            seen.add(page_id)
            response = self.client.get(f"/pages/{page_id}")
            response.raise_for_status()
            _append_item(items, self._page_item(response.json()))
            for block in self._blocks(page_id):
                if block.get("type") == "child_page" and block.get("id"):
                    pending.append(str(block["id"]))
        return items

    def _blocks(self, block_id: str, *, depth: int = 0) -> list[dict[str, Any]]:
        if depth > 20:
            raise ValueError("Notion block tree exceeds the supported depth")
        blocks: list[dict[str, Any]] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            response = self.client.get(f"/blocks/{block_id}/children", params=params)
            response.raise_for_status()
            data = response.json()
            for block in data.get("results", []):
                blocks.append(block)
                if len(blocks) > MAX_EXTERNAL_ITEMS:
                    raise ValueError("Notion page exceeds the 20000 block limit")
                if block.get("has_children") and block.get("id"):
                    blocks.extend(self._blocks(str(block["id"]), depth=depth + 1))
                    if len(blocks) > MAX_EXTERNAL_ITEMS:
                        raise ValueError("Notion page exceeds the 20000 block limit")
            if not data.get("has_more"):
                return blocks
            cursor = str(data.get("next_cursor", ""))
            if not cursor:
                raise ValueError("Notion block pagination returned no cursor")

    def fetch(self, item: ExternalItem) -> bytes:
        lines = [f"# {item.title}"]
        for block in self._blocks(item.external_id):
            block_type = str(block.get("type", ""))
            text = _rich_text(block)
            if text:
                if block_type.startswith("heading_"):
                    level = min(6, int(block_type.rsplit("_", 1)[1]))
                    lines.append(f"{'#' * level} {text}")
                elif block_type in {"bulleted_list_item", "numbered_list_item", "to_do"}:
                    lines.append(f"- {text}")
                elif block_type == "quote":
                    lines.append(f"> {text}")
                elif block_type == "code":
                    language = str(block.get("code", {}).get("language", ""))
                    lines.extend([f"```{language}", text, "```"])
                else:
                    lines.append(text)
        return ("\n\n".join(lines) + "\n").encode()

    def close(self) -> None:
        self.client.close()


def validate_public_https_base_url(
    value: str,
    *,
    skip_network: bool = False,
    allow_private_host: bool = False,
) -> str:
    return resolve_public_endpoint(
        value,
        skip_network=skip_network,
        allow_private_host=allow_private_host,
    ).url


class ConfluenceConnector:
    def __init__(
        self,
        config: dict[str, Any],
        credential: dict[str, str],
        *,
        transport: httpx.BaseTransport | None = None,
        skip_network_validation: bool = False,
    ) -> None:
        endpoint = resolve_public_endpoint(
            str(config.get("base_url", "")),
            skip_network=skip_network_validation,
            allow_private_host=True,
        )
        self.base_url = endpoint.url
        self.space_key = str(config.get("space_key", "")).strip()
        self.root_page_id = str(config.get("root_page_id", "")).strip()
        self.deployment = str(config.get("deployment", "cloud")).strip()
        if not self.space_key:
            raise ValueError("Confluence requires space_key")
        email = credential.get("email", "").strip()
        api_token = credential.get("api_token", "").strip()
        personal_token = credential.get("personal_access_token", "").strip()
        if self.deployment == "cloud":
            if not email or not api_token:
                raise ValueError("Confluence Cloud credential requires email and api_token")
            auth: httpx.Auth | None = httpx.BasicAuth(email, api_token)
            headers = {"Accept": "application/json"}
        else:
            if not personal_token:
                raise ValueError("Confluence Data Center requires personal_access_token")
            auth = None
            headers = {"Authorization": f"Bearer {personal_token}", "Accept": "application/json"}
        client_transport = transport
        if client_transport is None:
            client_transport = build_pinned_httpx_transport(endpoint)
        self.client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            auth=auth,
            timeout=30,
            transport=client_transport,
            follow_redirects=False,
            trust_env=False,
        )

    def test_connection(self) -> None:
        response = self.client.get(
            "/rest/api/space", params={"spaceKey": self.space_key, "limit": 1}
        )
        response.raise_for_status()

    def list_items(self) -> list[ExternalItem]:
        items: list[ExternalItem] = []
        start = 0
        while True:
            response = self.client.get(
                "/rest/api/content",
                params={
                    "spaceKey": self.space_key,
                    "type": "page",
                    "status": "current",
                    "expand": "version,ancestors",
                    "start": start,
                    "limit": 100,
                },
            )
            response.raise_for_status()
            data = response.json()
            for page in data.get("results", []):
                page_id = str(page.get("id", ""))
                ancestor_ids = {
                    str(ancestor.get("id", ""))
                    for ancestor in page.get("ancestors", [])
                    if isinstance(ancestor, dict)
                }
                if (
                    self.root_page_id
                    and page_id != self.root_page_id
                    and self.root_page_id not in ancestor_ids
                ):
                    continue
                title = str(page.get("title", "Untitled"))
                version = page.get("version", {})
                webui = str(page.get("_links", {}).get("webui", ""))
                _append_item(
                    items,
                    ExternalItem(
                        external_id=page_id,
                        path=f"confluence/{self.space_key}/{page_id}.md",
                        title=title,
                        filename=f"{page_id}.md",
                        mime_type="text/markdown",
                        revision=str(version.get("number", "")),
                        modified_at=str(version.get("when", "")),
                        url=f"{self.base_url}{webui}" if webui else "",
                    )
                )
            if not data.get("_links", {}).get("next"):
                break
            start += int(data.get("size", 0))
            if int(data.get("size", 0)) <= 0:
                raise ValueError("Confluence pagination returned an empty next page")
        return items

    def fetch(self, item: ExternalItem) -> bytes:
        response = self.client.get(
            f"/rest/api/content/{item.external_id}",
            params={"expand": "body.storage"},
        )
        response.raise_for_status()
        data = response.json()
        html = str(data.get("body", {}).get("storage", {}).get("value", ""))
        soup = BeautifulSoup(html, "html.parser")
        lines = [f"# {item.title}"]
        for node in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
            text = node.get_text(" ", strip=True)
            if not text:
                continue
            if node.name and node.name.startswith("h"):
                lines.append(f"{'#' * int(node.name[1])} {text}")
            elif node.name == "li":
                lines.append(f"- {text}")
            else:
                lines.append(text)
        return ("\n\n".join(lines) + "\n").encode()

    def close(self) -> None:
        self.client.close()


def build_connector(source) -> Connector:
    config = json.loads(source.config_json or "{}")
    credential = resolve_connector_credential(source.credential_ref)
    if source.provider == "aws_s3":
        return S3Connector(config, credential)
    if source.provider == "tencent_cos":
        return TencentCosConnector(config, credential)
    if source.provider == "notion":
        return NotionConnector(config, credential)
    if source.provider == "confluence":
        return ConfluenceConnector(config, credential)
    raise ValueError(f"Unsupported external source provider: {source.provider}")
