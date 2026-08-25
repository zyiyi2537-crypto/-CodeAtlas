from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from codeatlas.embeddings import EmbeddingClient
from codeatlas.settings import Settings


def tencent_settings(tmp_path: Path, dimension: int = 4) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        project_root=tmp_path,
        data_dir=data_dir,
        database_url="mysql+pymysql://unused@127.0.0.1/unused",
        repositories_dir=data_dir / "repositories",
        chroma_path=data_dir / "chroma",
        environment="test",
        public_origin="http://testserver",
        cookie_secure=False,
        allowed_git_hosts=("github.com",),
        mcp_allowed_hosts=("testserver",),
        embedding_mode="tencent_multimodal",
        embedding_base_url="https://tokenhub.tencentmaas.com/v1",
        embedding_api_key="test-key",
        embedding_model="kinfra-vl-embedding-2b",
        embedding_dimension=dimension,
        llm_base_url="",
        llm_api_key="",
        llm_model="test",
        max_repository_mb=20,
        max_source_files=100,
        git_timeout_seconds=30,
    )


def test_tencent_multimodal_embeds_each_text_with_provider_schema(
    tmp_path: Path, monkeypatch
) -> None:
    requests: list[dict] = []

    def fake_post(url, *, headers, json, timeout):
        requests.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        index = len(requests)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"data": [{"embedding": [float(index), 0.0, 0.0, 0.0], "index": 0}]},
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    vectors = EmbeddingClient(tencent_settings(tmp_path)).embed(["第一段", "第二段"])

    assert vectors == [[1.0, 0.0, 0.0, 0.0], [2.0, 0.0, 0.0, 0.0]]
    assert [item["url"] for item in requests] == [
        "https://tokenhub.tencentmaas.com/v1/embeddings/multimodal",
        "https://tokenhub.tencentmaas.com/v1/embeddings/multimodal",
    ]
    assert requests[0]["json"] == {
        "model": "kinfra-vl-embedding-2b",
        "input": [{"type": "text", "text": "第一段"}],
        "instructions": "生成适合文本检索的向量",
    }


def test_tencent_multimodal_accepts_top_level_embedding_response(
    tmp_path: Path, monkeypatch
) -> None:
    def fake_post(url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"embedding": [0.1, 0.2, 0.3, 0.4]},
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    assert EmbeddingClient(tencent_settings(tmp_path)).embed(["探测"])[0] == [
        0.1,
        0.2,
        0.3,
        0.4,
    ]


def test_tencent_multimodal_rejects_unexpected_dimension(
    tmp_path: Path, monkeypatch
) -> None:
    def fake_post(url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"data": [{"embedding": [0.1, 0.2], "index": 0}]},
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(ValueError, match="unexpected dimension"):
        EmbeddingClient(tencent_settings(tmp_path)).embed(["探测"])
