"""验收回归层：明确做攻略必须走草稿和集中补问，不退成未核实的自由回答。"""

import json
from contextlib import nullcontext
from datetime import date
from unittest.mock import Mock
from uuid import uuid4

from app.schemas.attachment import AttachmentSnapshot
from app.schemas.requirement.conversation import ConversationState
from app.schemas.requirement.history import SavedRequirementMessage
from app.services.chat.service import process_saved_message
from app.services.document.vector_store import MilvusError
from app.services.requirement.extract import understand_turn
from app.services.requirement.history import RequirementHistoryService
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID, RecordingModel, answer, understanding

"""用途兜底测试函数：带文件明确要求生成攻略时，不因模型漏填用途重复追问。"""

def test_attachment_plan_defaults_to_reference_when_use_omitted():
    raw = understanding(answer(destination="长沙", origin="银川", days=3),
                        response_mode="plan", destination_action="set")
    _, interpreted = understand_turn("我想从银川去长沙玩三天，给我做一份攻略",
        RecordingModel([json.dumps(raw)]), reference_date=date(2026, 9, 20),
        previous=None, conversation=ConversationState(), history_messages=[],
        attachments_selected=True,
        attachments=[AttachmentSnapshot(id=uuid4(), file_name="长沙.pdf")])
    assert interpreted.attachment_use.mode == "reference"
    assert interpreted.attachment_use.apply_to_plan


"""旧附件测试函数：另开攻略时不把可用历史附件自动当作本轮要求。"""

def test_previous_attachment_is_not_implicitly_selected_for_another_plan():
    raw = understanding(answer(destination="杭州", days=3),
                        response_mode="plan", destination_action="set")
    _, interpreted = understand_turn("不要用刚才文件，做杭州攻略",
        RecordingModel([json.dumps(raw)]), reference_date=date(2026, 9, 20),
        previous=None, conversation=ConversationState(), history_messages=[],
        attachments=[AttachmentSnapshot(id=uuid4(), file_name="长沙.pdf")])
    assert interpreted.attachment_use is None


"""集中补问测试函数：缺少人数预算时不调用普通回答，也不丢失目的地和天数。"""

def test_explicit_plan_missing_fields_asks_once_instead_of_free_answer(store_engine):
    trip = TripService(store_engine).create_session("验收", user_id=TEST_USER_ID)
    raw = understanding(answer(destination="长沙", origin="银川", days=3),
                        response_mode="plan", destination_action="set")
    model = RecordingModel([json.dumps(raw)])
    search = Mock()
    result = process_saved_message(trip.id, SavedRequirementMessage(
        message="银川去长沙玩三天，做一份攻略", message_id=uuid4(), expected_revision=0),
        model, RequirementHistoryService(store_engine), "test", nullcontext(search), Mock())
    assert result.response.itinerary is None
    assert result.response.status == "needs_clarification"
    assert "几个人" in result.response.reply and "预算" in result.response.reply
    assert result.response.result.extraction.destination == "长沙"
    assert result.response.result.extraction.days == 3
    assert len(model.calls) == 1
    search.search.assert_not_called()


"""故障测试函数：规划的知识库失败明确提示重试，不能伪装成已完成攻略。"""

def test_explicit_plan_rag_failure_does_not_invent_prices(store_engine, monkeypatch):
    trip = TripService(store_engine).create_session("检索失败", user_id=TEST_USER_ID)
    raw = understanding(answer(destination="长沙", days=3, travelers=1, total_budget="4000"),
                        response_mode="plan", destination_action="set")
    model = RecordingModel([json.dumps(raw)])
    monkeypatch.setattr("app.services.chat.service.plan_trip",
                        Mock(side_effect=MilvusError("offline")))
    saved = process_saved_message(trip.id, SavedRequirementMessage(
        message="长沙三天，一人4000元，做攻略", message_id=uuid4(), expected_revision=0),
        model, RequirementHistoryService(store_engine), "test", nullcontext(Mock()), Mock())
    assert saved.response.itinerary is None
    assert saved.response.status == "needs_clarification"
    assert "知识库" in saved.response.reply and "重试" in saved.response.reply
    assert len(model.calls) == 1
