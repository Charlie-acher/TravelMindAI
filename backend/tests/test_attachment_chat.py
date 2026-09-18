"""附件聊天测试层：验证幂等、跨会话拒绝和读取附件不修改原行程。"""

from contextlib import nullcontext
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.attachment import AttachmentAnalysis, AttachmentSnapshot
from app.schemas.requirement.history import (
    RequirementHistory,
    SavedRequirementMessage,
    SavedRequirementTurn,
)
from app.services.chat.service import process_saved_message
from app.services.requirement.history import HistoryConflictError
from tests.test_chat_context import _turn

"""附件消息合同测试函数：旧请求兼容空列表，拒绝重复编号和超出单轮数量。"""

def test_attachment_message_limits():
    args = {"message": "读取附件", "message_id": uuid4(), "expected_revision": 0}
    assert SavedRequirementMessage(**args).attachment_ids == []
    identifier = uuid4()
    for ids in ([identifier] * 2, [uuid4() for _ in range(4)]):
        with pytest.raises(ValidationError):
            SavedRequirementMessage(**args, attachment_ids=ids)


"""附件轮次测试函数：先识别再一次保存，已提交重试不调用识别或行程生成。"""

def test_attachment_turn_preserves_state_and_retries():
    prior = _turn(1, "想去苏州", "苏州旅行")
    history = RequirementHistory(session_id=uuid4(), revision=1, turns=[prior])
    store, reader, model, maps, search = Mock(), Mock(), Mock(), Mock(), Mock()
    store.read.return_value = history
    store.read_plan.return_value = (2, Mock())
    item = AttachmentSnapshot(id=uuid4(), file_name="杭州.txt", analysis=AttachmentAnalysis(
        city="杭州", summary="杭州路线", warnings=["顺序不明确"],
    ))
    reader.read.return_value = [item]

    """提交替身函数：保存后追加到可恢复历史。"""

    def append(session_id, message_id, revision, response):
        turn = SavedRequirementTurn(message_id=message_id, revision=revision + 1, response=response)
        history.turns.append(turn)
        history.revision += 1
        return turn

    store.append.side_effect = append
    payload = SavedRequirementMessage(message="把路线放第二天", message_id=uuid4(),
                                      expected_revision=1, attachment_ids=[item.id])
    saved = process_saved_message(history.session_id, payload, model, store, "qa",
                                  nullcontext(search), maps, attachments=reader)
    assert saved.response.attachments == [item]
    assert saved.response.result.extraction == prior.response.result.extraction
    assert saved.response.itinerary is None
    assert "尚未修改" in saved.response.reply
    model.generate_json.assert_not_called()
    search.search.assert_not_called()
    store.append.assert_called_once()
    assert process_saved_message(history.session_id, payload, model, store, "retry",
                                 nullcontext(search), maps, attachments=reader) == saved
    reader.read.assert_called_once()
    changed = payload.model_copy(update={"attachment_ids": [uuid4()]})
    with pytest.raises(HistoryConflictError):
        process_saved_message(history.session_id, changed, model, store, "changed",
                              nullcontext(search), maps, attachments=reader)


"""权限失败测试函数：附件拒绝和旧版本冲突不被当成普通识别失败提交。"""

@pytest.mark.parametrize("conflict", [False, True])
def test_attachment_ownership_and_revision_precede_model(conflict):
    store, reader, model = Mock(), Mock(), Mock()
    history = RequirementHistory(session_id=uuid4(), revision=1 if conflict else 0, turns=[])
    store.read.return_value = history
    store.read_plan.return_value = (0, None)
    reader.read.side_effect = HTTPException(404, "附件不存在")
    payload = SavedRequirementMessage(message="看附件", message_id=uuid4(), expected_revision=0,
                                      attachment_ids=[uuid4()])
    with pytest.raises(HistoryConflictError if conflict else HTTPException):
        process_saved_message(history.session_id, payload, model, store, "qa", nullcontext(Mock()),
                              Mock(), attachments=reader)
    store.append.assert_not_called()
    model.generate_json.assert_not_called()
    if conflict:
        reader.read.assert_not_called()
