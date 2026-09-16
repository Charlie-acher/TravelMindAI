"""测试层：检查资料切分不丢字、位置准确，以及片段保存和接口读取。"""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.document.routes import get_document_service
from app.config import Settings
from app.main import create_app
from app.schemas.document.base import ParsedSection
from app.services.document.service import DocumentService

"""切分测试函数：长文字不得丢失，重叠和位置必须能对回原文。"""


@pytest.mark.parametrize("text", ["短攻略", "游" * 800, "游" * 801, "杭州🙂 " * 701],
                         ids=["short", "exact", "over", "unicode"])
def test_chunks_cover_original_text(text: str) -> None:
    from app.services.document.chunker import split_sections

    chunks = split_sections([ParsedSection(text=text, order=1, page_number=2)])
    rebuilt = ""
    previous_end = 0
    for index, chunk in enumerate(chunks, 1):
        assert chunk.order == index and chunk.section_order == 1
        assert chunk.page_number == 2
        assert 0 < len(chunk.text) <= 800
        assert chunk.text == text[chunk.start_char:chunk.end_char]
        assert chunk.start_char == (previous_end - 100 if previous_end else 0)
        rebuilt += chunk.text[previous_end - chunk.start_char:]
        previous_end = chunk.end_char
    assert rebuilt == text


"""位置测试函数：优先在段落或句末切开，不把不同页和标题的正文混在一起。"""


def test_chunks_keep_boundaries_and_locations() -> None:
    from app.services.document.chunker import split_sections

    sections = [
        ParsedSection(text="甲" * 500 + "。\n" + "乙" * 700, order=1,
                      page_number=3, section_path=["杭州", "交通"]),
        ParsedSection(text="另一页", order=2, page_number=4, section_path=["住宿"]),
    ]
    chunks = split_sections(sections)
    assert chunks[0].text.endswith("。\n")
    assert chunks[0].end_char == 502
    assert chunks[0].section_path == ["杭州", "交通"]
    assert chunks[-1].section_order == 2 and chunks[-1].page_number == 4
    assert chunks[-1].section_path == ["住宿"] and chunks[-1].start_char == 0
    assert split_sections([]) == []


"""接口测试函数：已有资料按需切分，分页和刷新读回同一批编号，越权或失败资料被拒绝。"""


def test_chunk_api_persistence_and_errors(store_engine: Engine, tmp_path: Path) -> None:
    service = DocumentService(store_engine, tmp_path, "local-demo")
    saved = service.upload(BytesIO(("游" * 2000).encode()), "长攻略.txt", "text/plain")
    failed = service.upload(BytesIO(b"broken"), "坏资料.pdf", "application/pdf")
    other = DocumentService(store_engine, tmp_path, "other-owner").upload(
        BytesIO(b"private"), "private.txt", "text/plain"
    )
    app = create_app(Settings(database_url=None))
    app.dependency_overrides[get_document_service] = lambda: service
    path = f"/api/v1/documents/{saved.document.id}/chunks"
    with TestClient(app) as client:
        empty = client.get(path)
        assert empty.status_code == 200 and empty.json() == {"total": 0, "items": []}
        first = client.post(path)
        assert first.status_code == 200
        body = first.json()
        assert body["total"] == 3
        assert len({chunk["id"] for chunk in body["items"]}) == 3
        assert all(chunk["document_id"] == str(saved.document.id) for chunk in body["items"])
        assert client.post(path).json() == body
        assert client.get(path + "?limit=1&offset=1").json() == {
            "total": 3, "items": body["items"][1:2],
        }
        assert client.get(path + "?offset=99").json() == {"total": 3, "items": []}
        assert client.get(path + "?limit=0").status_code == 422
        assert client.get(path + "?offset=-1").status_code == 422
        for method in [client.get, client.post]:
            for missing in [other.document.id, uuid4()]:
                assert method(f"/api/v1/documents/{missing}/chunks").status_code == 404
            response = method(f"/api/v1/documents/{failed.document.id}/chunks")
            assert response.status_code == 409
            assert response.json()["error"]["message"]
    app.dependency_overrides[get_document_service] = lambda: DocumentService(
        store_engine, tmp_path, "local-demo"
    )
    with TestClient(app) as refreshed:
        assert refreshed.get(path).json() == body


"""并发测试函数：同时生成同一资料时，只保存一批片段，编号保持一致。"""


def test_concurrent_chunk_generation(store_engine: Engine, tmp_path: Path) -> None:
    service = DocumentService(store_engine, tmp_path, "local-demo")
    saved = service.upload(BytesIO(("西湖" * 1000).encode()), "攻略.txt", "text/plain")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.generate_chunks(saved.document.id), range(2)))
    assert results[0] == results[1]
    assert service.list_chunks(saved.document.id).total == 3


"""体积限制测试函数：超长标题随片段重复时，必须在写库前拒绝，防止结果膨胀。"""


def test_repeated_heading_budget(store_engine: Engine, tmp_path: Path) -> None:
    from app.services.document.chunker import split_sections
    from app.services.document.parser import parse_document

    content = ("# " + "题" * 490000).encode()
    # 先直接检查切分上限；缺少限制时在这里失败，避免测试真的向数据库写入巨型结果。
    with pytest.raises(ValueError, match="缩短标题"):
        split_sections(parse_document(content, ".md").sections)
    service = DocumentService(store_engine, tmp_path, "local-demo")
    saved = service.upload(BytesIO(content), "长标题.md", "text/markdown")
    assert saved.document.status == "parsed"
    app = create_app(Settings(database_url=None))
    app.dependency_overrides[get_document_service] = lambda: service
    path = f"/api/v1/documents/{saved.document.id}/chunks"
    with TestClient(app) as client:
        response = client.post(path)
        assert response.status_code == 422
        assert "缩短标题" in response.json()["error"]["message"]
        assert client.get(path).json() == {"total": 0, "items": []}
        assert service.read(saved.document.id).status == "parsed"
