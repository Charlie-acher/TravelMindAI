"""测试层：验证管理员资料的两个标签、筛选计数和稳定分页。"""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, insert, select

from app.models.auth import User
from app.models.document import DocumentRecord
from app.schemas.document.base import DocumentMetadata
from app.services.document.metadata import infer_metadata
from app.services.document.service import DocumentService


def test_business_metadata_only() -> None:
    assert DocumentMetadata(city=" 杭州市 ").city == "杭州"
    assert DocumentMetadata(city=" 市 ").city is None
    for field in ("source", "review_status", "poi_id"):
        with pytest.raises(ValidationError):
            DocumentMetadata.model_validate({field: "value"})
    assert infer_metadata("杭州住宿指南.txt", []).category == "住宿"
    assert infer_metadata("杭州综合指南.txt", []).category is None


def test_filtered_city_counts_and_cursor(store_engine: Engine, tmp_path: Path) -> None:
    service = DocumentService(store_engine, tmp_path, "knowledge-base")
    for name, body in (("杭州住宿甲.txt", "甲"), ("杭州住宿乙.txt", "乙"),
                       ("杭州景点.txt", "丙"), ("未知.txt", "丁")):
        service.upload(BytesIO(body.encode()), name, "text/plain")
    assert service.city_counts(category="住宿") == [{"city": "杭州", "total": 2}]
    page = service.page(limit=1, city="杭州", category="住宿")
    assert len(page.items) == 1 and page.next_cursor
    next_page = service.page(limit=1, city="杭州", category="住宿", cursor=page.next_cursor)
    assert len(next_page.items) == 1 and next_page.next_cursor is None
    assert page.items[0].id != next_page.items[0].id
    assert service.city_counts(unclassified_city=True) == [{"city": None, "total": 1}]
    assert service.city_counts(query="%") == []  # 文件名按字面查找，不把%当通配符。


def test_upload_records_actual_admin(store_engine: Engine, tmp_path: Path) -> None:
    admin_id = uuid4()
    with store_engine.begin() as connection:
        connection.execute(insert(User).values(id=admin_id, username="document-admin",
                                               password_hash="test", role="admin"))
    service = DocumentService(store_engine, tmp_path, "knowledge-base")
    saved = service.upload(BytesIO(b"hotel"), "杭州住宿.txt", "text/plain",
                           uploaded_by=admin_id).document
    with store_engine.connect() as connection:
        assert connection.scalar(select(DocumentRecord.uploaded_by).where(
            DocumentRecord.id == saved.id,
        )) == admin_id
