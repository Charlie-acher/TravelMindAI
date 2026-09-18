"""存储验收层：在隔离 PostgreSQL 中验证话题和摘要随完整轮次原子保存。"""

from uuid import uuid4

import pytest
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session

from app.models.requirement_turn import RequirementTurn
from app.schemas.itinerary import UndoDraftRequest
from app.schemas.requirement.conversation import ConversationState, HistorySummary
from app.services.chat.context import latest_conversation, latest_summary
from app.services.requirement.history import HistoryConflictError, RequirementHistoryService
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID
from tests.test_itinerary_history import sample_plan, sample_response

"""带状态回复函数：为存储测试准备独立的话题和可选摘要快照。"""

def state_response(message="继续", *, city="苏州", covered=None, text="不爬山"):
    response = sample_response(message)
    response.conversation = ConversationState(topic_cities=[city] if city else [])
    if covered is not None:
        response.history_summary = HistorySummary(text=text, covered_revision=covered)
    return response


"""恢复测试函数：新服务读取原快照，空摘要有效，旧字段缺失和其他会话均不干扰。"""

def test_restart_restores_empty_summary_and_cleared_topic_with_legacy_rows(store_engine: Engine):
    trips = TripService(store_engine)
    trip = trips.create_session("状态恢复", user_id=TEST_USER_ID)
    other = trips.create_session("其他会话", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    first = service.append(trip.id, uuid4(), 0, state_response("先去苏州"))
    service.append(trip.id, uuid4(), 1, state_response("目的地未定", city=None,
                                                     covered=1, text=""))
    service.append(other.id, uuid4(), 0, state_response("去杭州", city="杭州"))
    # 模拟升级前的旧记录，读取时不能要求补字段或重新调用模型。
    with Session(store_engine) as unit, unit.begin():
        row = unit.scalar(select(RequirementTurn).where(
            RequirementTurn.message_id == first.message_id,
        ))
        payload = dict(row.response_json)
        payload.pop("conversation")
        payload.pop("history_summary")
        row.response_json = payload
    restored = RequirementHistoryService(store_engine).read(trip.id)
    assert restored.turns[0].response.conversation is None
    assert restored.turns[0].response.history_summary is None
    assert latest_conversation(restored.turns) == ConversationState()
    assert latest_summary(restored.turns) == HistorySummary(text="", covered_revision=1)
    assert latest_conversation(service.read(other.id).turns).topic_cities == ["杭州"]


"""覆盖版本测试函数：候选摘要不能超过读取版本，也不能使已保存进度回退。"""

@pytest.mark.parametrize("invalid_coverage", [0, 3])
def test_summary_rejects_invalid_coverage_without_mutating_state(
    store_engine: Engine, invalid_coverage: int,
):
    trip = TripService(store_engine).create_session("摘要版本", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    service.append(trip.id, uuid4(), 0, state_response("第一轮"))
    service.append(trip.id, uuid4(), 1, state_response("第二轮", covered=1))
    before = service.read(trip.id)
    with pytest.raises(HistoryConflictError):
        service.append(trip.id, uuid4(), 2, state_response(
            "无效摘要", city="杭州", covered=invalid_coverage,
        ))
    assert service.read(trip.id) == before


"""过期提交测试函数：版本冲突不覆盖较新的状态和摘要，原文保持完整。"""

def test_conflicting_summary_preserves_winning_snapshot(store_engine: Engine):
    trip = TripService(store_engine).create_session("摘要冲突", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    service.append(trip.id, uuid4(), 0, state_response("第一轮"))
    service.append(trip.id, uuid4(), 1, state_response("胜出", city="杭州", covered=1))
    before = service.read(trip.id)
    with pytest.raises(HistoryConflictError):
        service.append(trip.id, uuid4(), 1, state_response("过期", covered=1, text="旧摘要"))
    assert service.read(trip.id) == before


"""事务回滚测试函数：插入轮次后报错，话题、摘要、原文和会话时间一起回滚。"""

def test_failed_save_does_not_advance_summary_or_topic(store_engine: Engine):
    trips = TripService(store_engine)
    trip = trips.create_session("摘要回滚", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    service.append(trip.id, uuid4(), 0, state_response("第一轮"))
    service.append(trip.id, uuid4(), 1, state_response("第二轮", covered=1))
    before = service.read(trip.id)
    timestamp = trips.get_session(trip.id).updated_at

    """故障注入函数：已执行消息插入时主动报错，验证整个事务回滚。"""

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO requirement_turns"):
            raise RuntimeError("测试提交失败")

    event.listen(store_engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="测试提交失败"):
            service.append(trip.id, uuid4(), 2, state_response(
                "第三轮", city="杭州", covered=2, text="候选摘要",
            ))
    finally:
        event.remove(store_engine, "after_cursor_execute", fail)
    assert service.read(trip.id) == before
    assert trips.get_session(trip.id).updated_at == timestamp


"""重复提交测试函数：相同消息原样返回，不用重试携带的状态和摘要覆盖已存快照。"""

def test_duplicate_submission_returns_original_summary_and_topic(store_engine: Engine):
    trip = TripService(store_engine).create_session("摘要重试", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    service.append(trip.id, uuid4(), 0, state_response("第一轮"))
    message_id = uuid4()
    saved = service.append(trip.id, message_id, 1, state_response("第二轮", covered=1))
    retry = state_response("第二轮", city="杭州", covered=999, text="不得替换")
    assert service.append(trip.id, message_id, 1, retry) == saved
    assert service.read(trip.id).revision == 2


"""撤销状态测试函数：撤销只恢复行程条件，保留已确认的话题或明确清空状态。"""

@pytest.mark.parametrize("topic,legacy_target", [(None, False), ("苏州", False), (None, True)])
def test_undo_keeps_explicit_topic_and_saved_summary(store_engine: Engine, topic, legacy_target):
    trip = TripService(store_engine).create_session("撤销话题", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    service.append(trip.id, uuid4(), 0, state_response("第一版", city=topic), plan=sample_plan(),
                   expected_itinerary_version=0)
    changed = state_response("第二版", city=topic, covered=1)
    if legacy_target:
        changed.conversation = None
    second = service.append(trip.id, uuid4(), 1, changed,
                            plan=sample_plan(), expected_itinerary_version=1)
    payload = UndoDraftRequest(operation_id=uuid4(), target_message_id=second.message_id,
                               expected_revision=2, expected_itinerary_version=2)
    undone = service.undo(trip.id, payload, "undo")
    expected_topic = ConversationState(topic_cities=[topic] if topic else [])
    assert undone.response.conversation == expected_topic
    # 摘要未更新时不重复存储，恢复仍能找到上一份已提交摘要。
    assert undone.response.history_summary is None
    restored = RequirementHistoryService(store_engine).read(trip.id)
    assert latest_summary(restored.turns) == second.response.history_summary
    assert latest_conversation(restored.turns) == expected_topic
    assert service.undo(trip.id, payload, "retry") == undone
