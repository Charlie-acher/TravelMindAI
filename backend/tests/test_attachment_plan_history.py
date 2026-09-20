"""附件行程集成测试层：用隔离数据库验证接续、局部修改、失败保留和撤销。"""

import json
from contextlib import nullcontext
from datetime import date
from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.schemas.attachment import AttachmentUse
from app.schemas.itinerary import UndoDraftRequest
from app.schemas.requirement.chat import RequirementChatResponse
from app.schemas.requirement.history import SavedRequirementMessage
from app.services.attachment.reader import AttachmentReader
from app.services.attachment.storage import AttachmentService
from app.services.chat.service import process_saved_message
from app.services.itinerary.rules import build_plan
from app.services.requirement.extract import build_result
from app.services.requirement.history import HistoryConflictError, RequirementHistoryService
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID, RecordingModel, answer
from tests.test_attachment_planning import route
from tests.test_itinerary_agent import places, proposal, requirements

"""纯附件持久化测试函数：规划接续保存空原话，复用识别缓存且幂等重试不重复生成。"""

def test_attachment_only_plan_saved_and_retried(store_engine, tmp_path):
    trip = TripService(store_engine).create_session("新建对话", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    prior = RequirementChatResponse(result=build_result("安排杭州两天", date(2026, 9, 20),
        requirements()), reply="请上传攻略以继续规划。", status="needs_clarification",
        changed_fields=[], request_id="qa")
    service.append(trip.id, uuid4(), 0, prior)
    storage = AttachmentService(store_engine, tmp_path)
    item = storage.upload(trip.id, BytesIO("杭州路线".encode()), "攻略.txt", "text/plain")
    names = ["湖滨路步行街", "河坊街"]
    storage.save_analysis(trip.id, item.id, route(*names).analysis, None)
    model = RecordingModel([json.dumps({"tools": [
        {"tool": "map_lookup", "name": name, "source_ids": [f"a{item.id.hex}_{index}"]}
        for index, name in enumerate(names)]}), json.dumps({"days": [
            proposal(1, "p1").model_dump(), proposal(2, "p2").model_dump()]})])
    maps = Mock()
    maps.lookup.side_effect = [place.map for place in places().values()]
    payload = SavedRequirementMessage(message="", message_id=uuid4(), expected_revision=1,
                                      attachment_ids=[item.id])
    saved = process_saved_message(trip.id, payload, model, service, "qa", nullcontext(Mock()),
                                  maps, attachments=AttachmentReader(storage, model))
    assert saved.response.result.original_message == ""
    assert saved.response.itinerary is not None
    assert len(saved.response.itinerary.plan.days) == 2
    assert saved.response.result.extraction == requirements()
    assert service.read(trip.id).turns[-1] == saved
    assert process_saved_message(trip.id, payload, Mock(), service, "retry", nullcontext(Mock()),
                                  Mock(), attachments=Mock()) == saved


"""空原话标题测试函数：首次只有附件时采用文件名作为标题，原话仍为空。"""

def test_attachment_only_first_turn_title(store_engine, tmp_path):
    trip = TripService(store_engine).create_session("新建对话", user_id=TEST_USER_ID)
    storage = AttachmentService(store_engine, tmp_path)
    item = storage.upload(trip.id, BytesIO("杭州路线".encode()), "攻略.txt", "text/plain")
    storage.save_analysis(trip.id, item.id, route("河坊街").analysis, None)
    service = RequirementHistoryService(store_engine)
    model = RecordingModel([interpretation(AttachmentUse(mode="unclear"))])
    saved = process_saved_message(trip.id, SavedRequirementMessage(message="",
        message_id=uuid4(), expected_revision=0, attachment_ids=[item.id]), model, service,
        "qa", nullcontext(Mock()), Mock(), attachments=AttachmentReader(storage, model))
    assert saved.response.result.original_message == ""
    assert saved.response.status == "needs_clarification"
    assert TripService(store_engine).get_session(trip.id).title == "攻略.txt"

"""理解样例函数：用模型合同表达用途，测试不依赖关键词路由。"""

def interpretation(use, **changes):
    return json.dumps({"requirement_update": answer(intent="modify_trip", **changes),
                       "conversation": {}, "attachment_use": use.model_dump(),
                       "response_mode": "plan" if use.apply_to_plan else "chat"})


"""已存草稿函数：测试数据只写独立schema和临时附件目录。"""

def setup_plan(engine, directory, name="河坊街"):
    trip = TripService(engine).create_session("附件行程验收", user_id=TEST_USER_ID)
    service = RequirementHistoryService(engine)
    plan = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")], places(), None, None)
    response = RequirementChatResponse(result=build_result("安排两天", date(2026, 9, 20),
        requirements()), reply="已生成", status="complete", changed_fields=[], request_id="qa")
    service.append(trip.id, uuid4(), 0, response, plan=plan, expected_itinerary_version=0)
    storage = AttachmentService(engine, directory)
    item = storage.upload(trip.id, BytesIO("杭州路线：河坊街".encode()), "路线.txt", "text/plain")
    storage.save_analysis(trip.id, item.id, route(name).analysis, None)
    return trip, service, storage, item, plan


"""接续验收函数：读取后不重传附件即可替换第二天，重试不增版本，撤销恢复原稿。"""

def test_attachment_followup_plan_retry_and_undo(store_engine, tmp_path):
    trip, service, storage, item, old = setup_plan(store_engine, tmp_path)
    read_model = RecordingModel([interpretation(AttachmentUse(mode="read"))])
    read = SavedRequirementMessage(message="读取附件", message_id=uuid4(), expected_revision=1,
                                   attachment_ids=[item.id])
    process_saved_message(trip.id, read, read_model, service, "qa", nullcontext(Mock()), Mock(),
                           attachments=AttachmentReader(storage, read_model))
    assert service.read_plan(trip.id) == (1, old)
    use = AttachmentUse(mode="replace", apply_to_plan=True, target_days=[2])
    model = RecordingModel([interpretation(use), json.dumps({"tools": [{
        "tool": "map_lookup", "name": "河坊街", "source_ids": [f"a{item.id.hex}_0"]}]}),
        json.dumps({
        "days": [proposal(2, "p2", start="11:00").model_dump()],
    })])
    payload = SavedRequirementMessage(message="用刚才的附件替换第二天，11点开始",
                                      message_id=uuid4(), expected_revision=2)
    reader = AttachmentReader(storage, model)
    saved = process_saved_message(trip.id, payload, model, service, "qa", nullcontext(Mock()),
                                  Mock(), attachments=reader)
    snapshot = saved.response.itinerary
    assert snapshot is not None and snapshot.version == 2 and snapshot.can_undo
    assert snapshot.plan.days[0] == old.days[0]
    assert snapshot.plan.days[1].activities[0].start_time == "11:00"
    assert snapshot.plan.budget == old.budget
    assert saved.response.attachment_request_ids == []
    assert saved.response.attachments[0].id == item.id
    assert saved.response.attachment_use == use
    assert service.read(trip.id).turns[-1] == saved
    assert process_saved_message(trip.id, payload, Mock(), service, "retry", nullcontext(Mock()),
                                  Mock(), attachments=Mock()) == saved
    with pytest.raises(HistoryConflictError):
        process_saved_message(trip.id, payload.model_copy(update={"attachment_ids": [item.id]}),
                              Mock(), service, "retry", nullcontext(Mock()), Mock())
    restored = service.undo(trip.id, UndoDraftRequest(operation_id=uuid4(),
        target_message_id=payload.message_id, expected_revision=3,
        expected_itinerary_version=2), "undo")
    assert restored.response.itinerary.plan == old
    assert service.read_plan(trip.id)[0] == 3


"""失败保留函数：地图无法核对时不变更需求或草稿，仍保存用途和原件快照。"""

def test_attachment_failed_lookup_preserves_state(store_engine, tmp_path):
    trip, service, storage, item, old = setup_plan(store_engine, tmp_path, name="新景点")
    model = RecordingModel([interpretation(AttachmentUse(
        mode="replace", apply_to_plan=True, target_days=[2]), total_budget="6000"),
        json.dumps({"tools": [{"tool": "map_lookup", "name": "新景点",
                              "source_ids": [f"a{item.id.hex}_0"]}]}),
        json.dumps({"clarification": "附件地点尚未完成地图核对，请补充准确名称"})])
    maps = Mock()
    maps.lookup.return_value.status = "not_found"
    payload = SavedRequirementMessage(message="用附件替换第二天，预算6000", message_id=uuid4(),
                                      expected_revision=1, attachment_ids=[item.id])
    saved = process_saved_message(trip.id, payload, model, service, "qa", nullcontext(Mock()), maps,
                                  attachments=AttachmentReader(storage, model))
    assert saved.response.itinerary is None
    assert saved.response.status == "needs_clarification"
    assert saved.response.result.extraction == requirements()
    assert service.read_plan(trip.id) == (1, old)
    assert "地图核对" in saved.response.reply
