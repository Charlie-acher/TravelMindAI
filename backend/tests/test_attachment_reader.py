"""附件读取测试层：验证失败重试、成功复用和整组归属检查。"""

from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.schemas.attachment import AttachmentAnalysis, AttachmentView
from app.services.attachment.reader import AttachmentReader

"""原件替身函数：构造尚未识别的文字附件。"""

def uploaded(session_id):
    return AttachmentView(id=uuid4(), session_id=session_id, file_name="路线.txt",
                          mime_type="text/plain", size_bytes=6, status="uploaded",
                          created_at=datetime.now(timezone.utc))


"""失败与重试测试函数：失败结果进入快照，新请求成功后复用已保存结果。"""

def test_retry_failure_then_cache_success():
    sid = uuid4()
    item = uploaded(sid)
    storage, model = Mock(), Mock()
    storage.get.side_effect = lambda *args: item
    storage.read_content.return_value = ("杭州".encode(), "text/plain")

    """存储替身函数：保留每次成功或失败的状态，模拟重新打开原件。"""

    def save(session_id, aid, analysis, error):
        item.analysis, item.error_message = analysis, error
        item.status = "failed" if error else "ready"
        return item

    storage.save_analysis.side_effect = save
    model.generate_json.side_effect = ["broken", AttachmentAnalysis(
        city="杭州", summary="杭州备忘",
    ).model_dump_json()]
    reader = AttachmentReader(storage, model)
    first = reader.read(sid, [item.id])[0]
    assert first.size_bytes == item.size_bytes
    assert first.analysis is None and first.error_message
    second = reader.read(sid, [item.id])[0]
    assert second.analysis.city == "杭州" and second.error_message is None
    assert reader.read(sid, [item.id])[0] == second
    assert model.generate_json.call_count == 2
    assert first.analysis is None  # 原失败快照不随后续结果改写。


"""混入外部附件测试函数：整组归属不通过时，不能先读取或发送已通过的一份。"""

def test_validate_all_owners_before_processing():
    sid = uuid4()
    storage, model = Mock(), Mock()
    storage.get.side_effect = [uploaded(sid), HTTPException(404, "不可见")]
    with pytest.raises(HTTPException):
        AttachmentReader(storage, model).read(sid, [uuid4(), uuid4()])
    storage.read_content.assert_not_called()
    model.generate_json.assert_not_called()
