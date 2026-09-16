"""对话存储验收：使用独立PostgreSQL schema，模型结果由本地数据构造。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import Engine, event

from app.schemas.requirement.base import TravelRequestExtraction
from app.schemas.requirement.chat import RequirementChatResponse
from app.services.requirement.extract import build_result
from app.services.requirement.history import HistoryConflictError, RequirementHistoryService
from app.services.trip_service import SessionNotFoundError, TripService

"""创建有效但不完整的需求，用于证明保存不依赖必需字段全部填满。"""

def chat_response(message: str = "想去杭州") -> RequirementChatResponse:
    extraction = TravelRequestExtraction(
        intent="plan_trip",
        destination="杭州",
        origin=None,
        start_date=None,
        end_date=None,
        days=None,
        travelers=None,
        total_budget=None,
        pace=None,
        interests=[],
        dietary=[],
        lodging_preferences=[],
        hard_constraints=[],
        excluded_items=[],
        assumptions=[],
    )
    result = build_result(message, date(2026, 9, 11), extraction)
    return RequirementChatResponse(
        result=result,
        reply=result.clarification or "已齐",
        status="needs_clarification",
        changed_fields=["destination"],
        request_id="test-request",
    )


"""一次保存同时保留原话、回复和需求，换服务实例后仍能读取且不会串会话。"""

def test_history_survives_new_service_and_isolates_sessions(store_engine: Engine) -> None:
    trips = TripService(store_engine)
    first, second = trips.create_session("第一趟"), trips.create_session("第二趟")
    history = RequirementHistoryService(store_engine)
    source = chat_response()
    saved = history.append(first.id, uuid4(), 0, source)
    source.result.extraction.destination = "苏州"
    restored = RequirementHistoryService(store_engine).read(first.id)
    assert restored.turns[0].response.result.extraction.destination == "杭州"
    assert restored.turns[0].response.reply == saved.response.reply
    assert restored.revision == 1
    assert history.read(second.id).turns == []
    history.append(first.id, uuid4(), 1, chat_response("下一条"))
    assert [turn.revision for turn in history.read(first.id).turns] == [1, 2]


"""相同消息编号重试不重复写入；编号被用于不同原话或旧版本写入时明确拒绝。"""

def test_retry_and_stale_revision(store_engine: Engine) -> None:
    session = TripService(store_engine).create_session("重试")
    history = RequirementHistoryService(store_engine)
    message_id = uuid4()
    first = history.append(session.id, message_id, 0, chat_response())
    again = history.append(session.id, message_id, 0, chat_response())
    assert again == first
    with pytest.raises(HistoryConflictError):
        history.append(session.id, message_id, 0, chat_response("另一句话"))
    with pytest.raises(HistoryConflictError):
        history.append(session.id, uuid4(), 0, chat_response())
    assert history.read(session.id).revision == 1


"""两个请求都依据第0轮，只允许一个成功，另一个不能覆盖刚保存的结果。"""

def test_concurrent_stale_write_is_rejected(store_engine: Engine) -> None:
    session = TripService(store_engine).create_session("并发")
    history = RequirementHistoryService(store_engine)

    """把业务冲突变为可比较结果，其他异常仍然让测试失败。"""

    def submit() -> str:
        try:
            history.append(session.id, uuid4(), 0, chat_response())
            return "saved"
        except HistoryConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: submit(), range(2))) == ["conflict", "saved"]
    assert history.read(session.id).revision == 1


"""未知会话不能读取或保存，服务也不能悄悄创建一个新会话。"""

def test_missing_history_session(store_engine: Engine) -> None:
    history = RequirementHistoryService(store_engine)
    with pytest.raises(SessionNotFoundError):
        history.read(uuid4())
    with pytest.raises(SessionNotFoundError):
        history.append(uuid4(), uuid4(), 0, chat_response())


"""INSERT已经发出后模拟故障，整个事务回滚；再次保存仍然从第1轮开始。"""

def test_insert_failure_rolls_back_whole_turn(store_engine: Engine) -> None:
    trip = TripService(store_engine).create_session("事务回滚")
    history = RequirementHistoryService(store_engine)

    """只在本测试的新表INSERT后抛错，不影响其他SQL或真实业务schema。"""

    def fail_after_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO requirement_turns"):
            raise RuntimeError("模拟保存过程失败")

    event.listen(store_engine, "after_cursor_execute", fail_after_insert)
    try:
        with pytest.raises(RuntimeError, match="模拟保存过程失败"):
            history.append(trip.id, uuid4(), 0, chat_response())
    finally:
        event.remove(store_engine, "after_cursor_execute", fail_after_insert)
    assert history.read(trip.id).turns == []
    assert TripService(store_engine).get_session(trip.id).updated_at == trip.updated_at
    assert history.append(trip.id, uuid4(), 0, chat_response()).revision == 1
