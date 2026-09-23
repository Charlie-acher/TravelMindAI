"""附件聊天测试层：验证幂等、跨会话拒绝和读取附件不修改原行程。"""

import json
from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.llm.client import ModelOutputError
from app.schemas.attachment import AttachmentAnalysis, AttachmentSnapshot, AttachmentUse
from app.schemas.itinerary import PlanSource
from app.schemas.requirement.history import (
    RequirementHistory,
    SavedRequirementMessage,
    SavedRequirementTurn,
)
from app.services.chat.attachments import plan_attachment_candidates
from app.services.chat.service import process_saved_message
from app.services.itinerary.rules import build_plan
from app.services.requirement.history import HistoryConflictError
from tests.helpers import answer
from tests.test_attachment_plan_history import interpretation
from tests.test_chat_context import _turn
from tests.test_itinerary_agent import places, proposal, requirements

"""附件消息合同测试函数：旧请求兼容空列表，拒绝重复编号和超出单轮数量。"""

def test_attachment_message_limits():
    args = {"message": "读取附件", "message_id": uuid4(), "expected_revision": 0}
    assert SavedRequirementMessage(**args).attachment_ids == []
    identifier = uuid4()
    for ids in ([identifier] * 2, [uuid4() for _ in range(4)]):
        with pytest.raises(ValidationError):
            SavedRequirementMessage(**args, attachment_ids=ids)


"""纯附件合同测试函数：允许真实空原话，但没有原件时仍拒绝空提交。"""

def test_attachment_only_message_preserves_empty_text():
    args = {"message": "  ", "message_id": uuid4(), "expected_revision": 0}
    assert SavedRequirementMessage(**args, attachment_ids=[uuid4()]).message == ""
    with pytest.raises(ValidationError):
        SavedRequirementMessage(**args)


"""上传接续测试函数：等待攻略时直接接续已确认条件，不再要求用户重复下达规划指令。"""

@pytest.mark.parametrize("missing", [False, True])
def test_attachment_only_continues_pending_plan(missing):
    prior = _turn(1, "安排杭州两天", "请上传攻略，我会接着规划。")
    prior.response.result.extraction = requirements().model_copy(update={
        "travelers": None if missing else 3, "total_budget": None if missing else Decimal("4000"),
    })
    prior.response.status = "needs_clarification"
    history = RequirementHistory(session_id=uuid4(), revision=1, turns=[prior])
    store, model, reader = Mock(), Mock(), Mock()
    store.read.return_value, store.read_plan.return_value = history, (0, None)
    item = AttachmentSnapshot(id=uuid4(), file_name="攻略.pdf", analysis=AttachmentAnalysis(
        summary="内部识别概述", warnings=["内部待核对清单"], city="杭州"))
    reader.read.return_value = [item]
    payload = SavedRequirementMessage(message="", message_id=uuid4(), expected_revision=1,
                                      attachment_ids=[item.id])
    with patch("app.services.chat.attachments.plan_trip") as planner:
        planner.return_value = Mock(reply="行程已安排", plan=Mock())
        process_saved_message(history.session_id, payload, model, store, "qa",
                              nullcontext(Mock()), Mock(), attachments=reader)
    response = store.append.call_args.args[3]
    assert response.result.original_message == ""
    assert response.result.extraction == prior.response.result.extraction
    assert response.attachment_use == AttachmentUse(mode="reference", apply_to_plan=True)
    assert "内部" not in response.reply
    model.generate_json.assert_not_called()
    if missing:
        planner.assert_not_called()
        assert "几个人" in response.reply and "预算" in response.reply
    else:
        planner.assert_called_once()
        assert planner.call_args.args[1] == prior.response.result.extraction
        assert store.append.call_args.kwargs["plan"] is planner.return_value.plan


"""展示测试函数：识别证据留在快照内，普通附件回复不倾倒概述及核对清单。"""

def test_attachment_reply_keeps_analysis_internal():
    from app.services.attachment.reader import attachment_reply

    item = AttachmentSnapshot(id=uuid4(), file_name="攻略.pdf", analysis=AttachmentAnalysis(
        summary="内部长篇识别概述", warnings=["内部待核对地点"], city="杭州"))
    reply = attachment_reply([item])
    assert "攻略.pdf" in reply and "尚未修改" in reply
    assert "内部" not in reply


"""规划服务失败测试函数：内部模型错误不伪装成需要用户核对地点的任务。"""

def test_attachment_model_failure_is_not_a_place_confirmation_request():
    prior = _turn(1, "安排杭州两天", "请上传攻略")
    prior.response.result.extraction = requirements()
    prior.response.status = "needs_clarification"
    store, reader = Mock(), Mock()
    history = RequirementHistory(session_id=uuid4(), revision=1, turns=[prior])
    store.read.return_value, store.read_plan.return_value = history, (0, None)
    item = AttachmentSnapshot(id=uuid4(), file_name="攻略.txt")
    reader.read.return_value = [item]
    with patch("app.services.chat.attachments.plan_trip", side_effect=ModelOutputError(
            "规划模型没有返回有效的工具选择")):
        process_saved_message(history.session_id, SavedRequirementMessage(message="",
            message_id=uuid4(), expected_revision=1, attachment_ids=[item.id]), Mock(), store,
            "failure", nullcontext(Mock()), Mock(), attachments=reader)
    response = store.append.call_args.args[3]
    assert "服务" in response.reply
    assert "核对" not in response.reply and "工具选择" not in response.reply
    assert response.result.extraction == requirements()


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
    model.generate_json.return_value = json.dumps({
        "requirement_update": answer(intent="travel_info"), "conversation": {},
        "attachment_use": {"mode": "read", "apply_to_plan": False},
    })

    """提交替身函数：保存后追加到可恢复历史。"""

    def append(session_id, message_id, revision, response):
        turn = SavedRequirementTurn(message_id=message_id, revision=revision + 1, response=response)
        history.turns.append(turn)
        history.revision += 1
        return turn

    store.append.side_effect = append
    payload = SavedRequirementMessage(message="只读取这份附件", message_id=uuid4(),
                                      expected_revision=1, attachment_ids=[item.id])
    saved = process_saved_message(history.session_id, payload, model, store, "qa",
                                  nullcontext(search), maps, attachments=reader)
    assert saved.response.attachments == [item]
    assert saved.response.result.extraction == prior.response.result.extraction
    assert saved.response.itinerary is None
    assert "尚未修改" in saved.response.reply
    model.generate_json.assert_called_once()
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


"""缺少附件测试函数：旧附件指代无法接续时不得退回普通规划修改草稿。"""

def test_missing_referenced_attachment_never_falls_back_to_planning():
    prior = _turn(1, "聊点别的", "好的")
    prior.response.result.extraction = requirements()
    store, model = Mock(), Mock()
    store.read.return_value = RequirementHistory(session_id=uuid4(), revision=1, turns=[prior])
    store.read_plan.return_value = (1, Mock())
    model.generate_json.return_value = interpretation(AttachmentUse(
        mode="replace", apply_to_plan=True, target_days=[2]))
    payload = SavedRequirementMessage(message="用刚才附件替换第二天", message_id=uuid4(),
                                      expected_revision=1)
    with patch("app.services.chat.service.plan_trip") as ordinary:
        process_saved_message(store.read.return_value.session_id, payload, model, store, "qa",
                              nullcontext(Mock()), Mock())
    ordinary.assert_not_called()
    response = store.append.call_args.args[3]
    assert response.result.extraction == requirements()
    assert response.status == "needs_clarification"


"""共享问答测试函数：没有私人附件时，模型误填只读用途不能挡住知识库检索。"""

@pytest.mark.parametrize("mode", ["read", "reference"])
@pytest.mark.parametrize("intent", ["travel_info", "other"])
def test_shared_question_without_attachments_ignores_read_use(mode, intent):
    store, model, search = Mock(), Mock(), Mock()
    history = RequirementHistory(session_id=uuid4(), revision=0, turns=[])
    store.read.return_value, store.read_plan.return_value = history, (0, None)
    message = "苏州青苔旅行读书会的集合口令和签到地点是什么？请根据资料回答。"
    model.generate_json.return_value = json.dumps({
        "requirement_update": answer(intent=intent), "conversation": {},
        "attachment_use": {"mode": mode, "apply_to_plan": False},
    })
    with patch("app.services.chat.service.ground_chat_response",
               side_effect=lambda response, *args, **kwargs: response) as ground:
        process_saved_message(history.session_id, SavedRequirementMessage(message=message,
            message_id=uuid4(), expected_revision=0), model, store, "shared-question",
            nullcontext(search), Mock())
    ground.assert_called_once()
    assert message in ground.call_args.kwargs["retrieval_query"]
    response = store.append.call_args.args[3]
    assert response.attachment_use is None
    assert response.status == "knowledge"
    assert response.attachments == []


"""矛盾用途测试函数：规划标记与附件只读标记冲突时，缺少原件仍不得直接修改草稿。"""

def test_missing_attachment_with_conflicting_plan_flags_preserves_draft():
    prior = _turn(1, "聊点别的", "好的")
    prior.response.result.extraction = requirements()
    store, model = Mock(), Mock()
    store.read.return_value = RequirementHistory(session_id=uuid4(), revision=1, turns=[prior])
    store.read_plan.return_value = (1, Mock())
    raw = json.loads(interpretation(AttachmentUse(mode="reference", apply_to_plan=False)))
    raw["response_mode"] = "plan"
    model.generate_json.return_value = json.dumps(raw)
    payload = SavedRequirementMessage(message="请根据刚才附件重新生成行程", message_id=uuid4(),
                                      expected_revision=1)
    with patch("app.services.chat.service.plan_trip") as ordinary:
        process_saved_message(store.read.return_value.session_id, payload, model, store, "qa",
                              nullcontext(Mock()), Mock())
    ordinary.assert_not_called()
    response = store.append.call_args.args[3]
    assert response.result.extraction == requirements()
    assert response.status == "needs_clarification"


"""恢复测试函数：补问理解失败后附件与用途仍能由下一轮接续，不退成普通问答。"""

def test_invalid_followup_keeps_pending_attachment_and_requirements():
    prior = _turn(1, "根据附件做攻略", "几个人，预算多少？")
    prior.response.status = "needs_clarification"
    prior.response.attachment_use = AttachmentUse(mode="reference", apply_to_plan=True)
    prior.response.attachments = [AttachmentSnapshot(id=uuid4(), file_name="长沙.pdf")]
    store, model, search = Mock(), Mock(), Mock()
    history = RequirementHistory(session_id=uuid4(), revision=1, turns=[prior])
    store.read.return_value, store.read_plan.return_value = history, (0, None)
    model.generate_json.return_value = "invalid JSON"
    process_saved_message(history.session_id, SavedRequirementMessage(
        message="一个人4000元", message_id=uuid4(), expected_revision=1), model,
        store, "recover", nullcontext(search), Mock())
    response = store.append.call_args.args[3]
    assert response.attachments == prior.response.attachments
    assert response.attachment_use == prior.response.attachment_use
    assert response.result.extraction == prior.response.result.extraction
    assert response.status == "needs_clarification"
    model.generate_text.assert_not_called()
    search.search.assert_not_called()


"""已用攻略接续测试函数：修改草稿仍能使用没入选的附件地点，普通问答不触发规划。"""

@pytest.mark.parametrize("intervening", [False, True])
def test_plan_modification_keeps_full_original_attachment_candidates(intervening):
    item = AttachmentSnapshot(id=uuid4(), file_name="攻略.pdf", analysis=AttachmentAnalysis(
        city="杭州", summary="完整攻略", waypoints=[
            {"name": "湖滨路步行街", "evidence": "湖滨路步行街"},
            {"name": "灵隐寺", "evidence": "灵隐寺"},
        ]))
    old = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                     places(), None, None)
    old.days[0].activities[0].place.sources = [PlanSource(id=f"a{item.id.hex}_0",
        kind="attachment", attachment_id=item.id, title=item.file_name, text="湖滨路步行街")]
    prior = _turn(1, "按攻略做行程", "已生成")
    prior.response.attachments = [item, AttachmentSnapshot(
        id=uuid4(), file_name="无关资料.txt", analysis=AttachmentAnalysis(summary="未采用资料"))]
    prior.response.result.extraction = requirements()
    turns = [prior]
    if intervening:
        unrelated = _turn(2, "谢谢", "不客气")
        unrelated.response.result.extraction = requirements()
        turns.append(unrelated)
    store, model = Mock(), Mock()
    history = RequirementHistory(session_id=uuid4(), revision=len(turns), turns=turns)
    store.read.return_value, store.read_plan.return_value = history, (1, old)
    model.generate_json.return_value = json.dumps({"requirement_update": answer(
        intent="modify_trip"), "conversation": {}, "response_mode": "plan",
        "attachment_use": None})
    with patch("app.services.chat.service.plan_trip") as planner:
        planner.return_value = Mock(reply="已调整", plan=None)
        process_saved_message(history.session_id, SavedRequirementMessage(
            message="第二天还想去灵隐寺", message_id=uuid4(), expected_revision=len(turns)),
            model, store, "qa", nullcontext(Mock()), Mock())
    assert planner.call_args.kwargs["attachments"] == [item]
    assert planner.call_args.kwargs["attachment_use"].mode == "reference"
    assert plan_attachment_candidates(turns, old, "苏州") == []
    assert plan_attachment_candidates(turns, None, "杭州") == []
