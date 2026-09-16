"""测试层：检查试跑命令的无密钥提示、只读预览和真实资料取样路径。"""

import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine

from app.services.document.service import DocumentService

"""无密钥测试函数：明确提示配置缺失，不输出堆栈或误报成功。"""


def test_missing_configuration(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    from scripts import demo_embedding

    monkeypatch.delenv("TRAVELMIND_EMBEDDING_API_KEY", raising=False)
    assert demo_embedding.main(["--text", "杭州西湖"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "未配置" in output.err


"""预览测试函数：读取真实临时数据库的指定片段，不联网、不增删片段。"""


def test_document_dry_run(
    store_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    from scripts import demo_embedding

    service = DocumentService(store_engine, tmp_path, "knowledge-base")
    saved = service.upload(BytesIO(("西湖" * 1000).encode()), "杭州.txt", "text/plain")
    chunks = service.generate_chunks(saved.document.id)
    monkeypatch.setattr(demo_embedding, "create_database_engine", lambda settings: store_engine)

    """禁止网络函数：只读预览即使有配置也不应创建HTTP客户端。"""

    def reject_http(*args, **kwargs):
        raise AssertionError("dry-run不应联网")

    monkeypatch.setattr(demo_embedding.httpx, "Client", reject_http)
    assert (
        demo_embedding.main(
            [
                "--document-id",
                str(saved.document.id),
                "--limit",
                "1",
                "--offset",
                "1",
                "--dry-run",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "dry_run"
    assert output["chunk_ids"] == [str(chunks.items[1].id)]
    assert output["characters"] == [800]
    assert "vectors" not in output
    assert service.list_chunks(saved.document.id) == chunks


"""命令成功测试函数：只替换网络传输，检查命令经过真实客户端后输出摘要。"""


def test_cli_network_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    from scripts import demo_embedding

    config = tmp_path / "embedding.env"
    config.write_text(
        "TRAVELMIND_EMBEDDING_PROVIDER=aliyun\n"
        "TRAVELMIND_EMBEDDING_BASE_URL=https://embedding.example/compatible-mode/v1\n"
        "TRAVELMIND_EMBEDDING_MODEL=text-embedding-v4\n"
        "TRAVELMIND_EMBEDDING_API_KEY=private-key\n"
        "TRAVELMIND_EMBEDDING_DIMENSIONS=2\n"
        "TRAVELMIND_EMBEDDING_VERSION=fixture-v1\n",
        encoding="utf-8",
    )
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "object": "list",
                    "model": "text-embedding-v4",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.2, 0.3]}],
                    "usage": {"prompt_tokens": 3, "total_tokens": 3},
                },
            )
        )
    )
    monkeypatch.setattr(demo_embedding.httpx, "Client", lambda: http)
    assert demo_embedding.main(["--env-file", str(config), "--text", "西湖"]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["mode"] == "request_succeeded"
    assert result["vector_count"] == 1 and result["dimensions"] == 2
    assert "vectors" not in result and "private-key" not in output
    assert http.is_closed
