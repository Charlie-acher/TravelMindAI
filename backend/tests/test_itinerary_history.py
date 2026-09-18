"""存储验收层：在隔离PostgreSQL中验证逐日草稿与消息一起保存和撤销。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models.trip import Itinerary, TravelRequest
from app.schemas.budget import BudgetSummary
from app.schemas.document.answer import MapLookup
from app.schemas.itinerary import (
    PlanDay,
    PlannedActivity,
    PlanPlace,
    PlanSource,
    TravelPlan,
    UndoDraftRequest,
)
from app.schemas.requirement.base import TravelRequestExtraction
from app.schemas.requirement.chat import RequirementChatResponse
from app.services.auth import AuthService
from app.services.budget_service import calculate_budget
from app.services.requirement.extract import build_result
from app.services.requirement.history import HistoryConflictError, RequirementHistoryService
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID, answer

"""草稿样本函数：创建有来源和地图结果的两日行程，允许只修改第二天。"""

def sample_plan() -> TravelPlan:
    place = PlanPlace(
        id="west-lake", map=MapLookup(city="杭州", name="西湖", status="found"),
        sources=[PlanSource(id="1", kind="knowledge", title="杭州", text="西湖适合游览")],
    )
    return TravelPlan(title="杭州两日", destination="杭州", days=[
        PlanDay(day=day, activities=[PlannedActivity(place=place, start_time="09:00",
                                                   duration_minutes=90, transfer_minutes=30)])
        for day in (1, 2)
    ], budget=BudgetSummary.model_validate(calculate_budget(
        days=2, travelers=2, total_budget=Decimal("5000"))), warnings=["交通为预留建议"])


"""回复样本函数：保留日期、禁忌和排除项，以免撤销只恢复预算等部分字段。"""

def sample_response(message: str = "杭州两日", **changes: object) -> RequirementChatResponse:
    extraction = TravelRequestExtraction.model_validate(answer(
        destination="杭州", origin="上海", start_date="2026-10-01", end_date="2026-10-02",
        days=2, travelers=2, total_budget="5000", pace="relaxed", dietary=["不吃辣"],
        excluded_items=["爬山"], lodging_preferences=["安静"], hard_constraints=["无障碍"],
    ) | changes)
    return RequirementChatResponse(result=build_result(message, date(2026, 9, 16), extraction),
                                   reply="已生成草稿", status="complete", changed_fields=[],
                                   request_id="test")


"""首版与修改函数：同时保存两版行程和完整需求，返回可撤销目标。"""

def save_two(engine: Engine, user_id=TEST_USER_ID):
    trip = TripService(engine).create_session("新建对话", user_id=user_id)
    service = RequirementHistoryService(engine)
    first = service.append(trip.id, uuid4(), 0, sample_response(), plan=sample_plan(),
                           expected_itinerary_version=0)
    changed = first.response.itinerary.plan.model_copy(deep=True)
    changed.days[1].activities[0].start_time = "10:00"
    second = service.append(trip.id, uuid4(), 1, sample_response("第二天晚点", dietary=["素食"]),
                            plan=changed, expected_itinerary_version=1)
    payload = UndoDraftRequest(operation_id=uuid4(), target_message_id=second.message_id,
                               expected_revision=2, expected_itinerary_version=2)
    return trip, service, first, second, payload


"""版本保存测试函数：消息快照和数据库内容一致，摘要只指出实际修改的第二天。"""

def test_atomic_plan_snapshots_and_actual_changes(store_engine: Engine) -> None:
    trip, service, first, second, _ = save_two(store_engine)
    assert first.response.itinerary.operation == "create"
    assert not first.response.itinerary.can_undo
    snapshot = second.response.itinerary
    assert snapshot.operation == "modify" and snapshot.can_undo
    assert snapshot.previous_version == 1 and snapshot.version == 2
    assert any("第2天" in change for change in snapshot.changes)
    assert not any("第1天" in change for change in snapshot.changes)
    assert service.read_plan(trip.id) == (2, snapshot.plan)
    saved = TripService(store_engine).get_draft(trip.id)
    assert saved.requirement.request_json == second.response.result.extraction.model_dump(
        mode="json",
    )
    assert saved.itinerary.itinerary_json == snapshot.plan.model_dump(mode="json")


"""偏好摘要测试函数：行程不变时也展示真实需求变化，撤销使用数据库快照恢复摘要。"""

def test_requirement_only_changes_are_summarized_and_restored(store_engine: Engine) -> None:
    trip = TripService(store_engine).create_session("仅改偏好", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    plan = sample_plan()
    first = service.append(trip.id, uuid4(), 0, sample_response(), plan=plan,
                           expected_itinerary_version=0)
    response = sample_response("改吃素食", dietary=["素食"])
    response.changed_fields = ["destination"]  # 故意错误，摘要必须看实际快照。
    changed = service.append(trip.id, uuid4(), 1, response, plan=plan,
                             expected_itinerary_version=1)
    assert first.response.itinerary.changes == ["生成2天行程草稿"]
    assert changed.response.itinerary.changes == ["更新饮食偏好"]
    undone = service.undo(trip.id, UndoDraftRequest(
        operation_id=uuid4(), target_message_id=changed.message_id,
        expected_revision=2, expected_itinerary_version=2,
    ), "undo-preference")
    assert undone.response.itinerary.changes == ["恢复饮食偏好"]
    assert undone.response.result.extraction.dietary == ["不吃辣"]


"""单日摘要测试函数：只改活动时间时不额外生成未变化的需求摘要。"""

def test_day_only_change_keeps_existing_summary(store_engine: Engine) -> None:
    trip = TripService(store_engine).create_session("只改第二天", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    plan = sample_plan()
    service.append(trip.id, uuid4(), 0, sample_response(), plan=plan,
                   expected_itinerary_version=0)
    changed_plan = plan.model_copy(deep=True)
    changed_plan.days[1].activities[0].start_time = "10:00"
    changed = service.append(trip.id, uuid4(), 1, sample_response("第二天晚点"),
                             plan=changed_plan, expected_itinerary_version=1)
    assert changed.response.itinerary.changes == ["调整第2天的日期或活动安排"]


"""撤销恢复测试函数：恢复整份需求并新增草稿，重试原样返回，换参数明确冲突。"""

def test_undo_restores_full_requirement_and_is_idempotent(store_engine: Engine) -> None:
    trip, service, first, second, payload = save_two(store_engine)
    undone = service.undo(trip.id, payload, "undo-request")
    assert undone.revision == 3 and undone.message_id == payload.operation_id
    assert undone.response.result.extraction == first.response.result.extraction
    assert undone.response.itinerary.plan == first.response.itinerary.plan
    assert undone.response.itinerary.version == 3
    assert undone.response.itinerary.operation == "undo"
    assert not undone.response.itinerary.can_undo
    assert undone.response.changed_fields == ["dietary"]
    assert TripService(store_engine).get_draft(trip.id).itinerary.status == "draft"
    assert service.undo(trip.id, payload, "retry") == undone
    with pytest.raises(HistoryConflictError):
        service.undo(trip.id, payload.model_copy(update={"expected_revision": 3}), "different")
    with pytest.raises(HistoryConflictError):
        service.append(trip.id, payload.operation_id, 2, undone.response)
    with pytest.raises(HistoryConflictError):
        service.undo(trip.id, payload.model_copy(update={
            "operation_id": second.message_id,
        }), "reuse")
    assert service.read(trip.id).revision == 3


"""双版本测试函数：行程被另一入口保存或会话出现新消息后，旧按钮均不能生效。"""

@pytest.mark.parametrize("conflict", ["plan", "revision", "first", "undone"])
def test_undo_rejects_stale_or_ineligible_target(store_engine: Engine, conflict: str) -> None:
    trip, service, first, second, payload = save_two(store_engine)
    if conflict == "plan":
        TripService(store_engine).save_draft(trip.id, request_json={}, itinerary_json={})
    elif conflict == "revision":
        service.append(trip.id, uuid4(), 2, sample_response("再补个要求"))
        payload.expected_revision = 3
    elif conflict == "first":
        payload.target_message_id = first.message_id
    else:
        done = service.undo(trip.id, payload, "done")
        payload = UndoDraftRequest(operation_id=uuid4(), target_message_id=done.message_id,
                                   expected_revision=3, expected_itinerary_version=3)
    with pytest.raises(HistoryConflictError):
        service.undo(trip.id, payload, "rejected")


"""旧行程版本测试函数：即使消息revision未变化，也不覆盖从其他入口新增的草稿。"""

def test_append_checks_plan_version_and_preserves_legacy_version(store_engine: Engine) -> None:
    trip = TripService(store_engine).create_session("旧预算", user_id=TEST_USER_ID)
    TripService(store_engine).save_draft(trip.id, request_json={}, itinerary_json={"budget": {}})
    service = RequirementHistoryService(store_engine)
    assert service.read_plan(trip.id) == (1, None)
    with pytest.raises(HistoryConflictError):
        service.append(trip.id, uuid4(), 0, sample_response(), plan=sample_plan(),
                       expected_itinerary_version=0)
    saved = service.append(trip.id, uuid4(), 0, sample_response(), plan=sample_plan(),
                           expected_itinerary_version=1)
    assert saved.response.itinerary.version == 2
    assert saved.response.itinerary.operation == "create"
    assert not saved.response.itinerary.can_undo


"""事务回滚测试函数：消息写入后失败时，需求、行程和会话时间也一起回滚。"""

@pytest.mark.parametrize("operation", ["append", "undo"])
def test_plan_and_turn_failure_roll_back_together(store_engine: Engine, operation: str) -> None:
    trip, service, _, _, payload = save_two(store_engine)
    timestamp = TripService(store_engine).get_session(trip.id).updated_at

    """故障注入函数：确认前两张表已写入后，让第三张表报错。"""

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO requirement_turns"):
            raise RuntimeError("消息提交失败")

    event.listen(store_engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="消息提交失败"):
            if operation == "undo":
                service.undo(trip.id, payload, "failure")
            else:
                service.append(trip.id, uuid4(), 2, sample_response(), plan=sample_plan(),
                               expected_itinerary_version=2)
    finally:
        event.remove(store_engine, "after_cursor_execute", fail)
    assert service.read(trip.id).revision == 2
    assert service.read_plan(trip.id)[0] == 2
    with Session(store_engine) as unit:
        assert unit.scalar(select(func.count()).select_from(TravelRequest)) == 2
        assert unit.scalar(select(func.count()).select_from(Itinerary)) == 2
    assert TripService(store_engine).get_session(trip.id).updated_at == timestamp


"""撤销权限测试函数：真实Cookie和归属依赖拒绝跨账号与缺少CSRF请求头的请求。"""

def test_undo_route_enforces_owner_and_csrf(store_engine: Engine) -> None:
    trip, service, _, _, payload = save_two(store_engine)
    auth = AuthService(store_engine)
    auth.create_user("undo-other", "test-pass123", "user")
    settings = Settings(environment="test", database_url=SecretStr(
        store_engine.url.render_as_string(hide_password=False)))
    with TestClient(create_app(settings)) as client:
        headers = {"X-Requested-With": "TravelMindAI"}
        assert client.post("/api/v1/auth/login", headers=headers, json={
            "username": "undo-other", "password": "test-pass123",
        }).status_code == 200
        url = f"/api/v1/sessions/{trip.id}/drafts/undo"
        assert client.post(url, json=payload.model_dump(mode="json")).status_code == 403
        assert client.post(url, headers=headers, json=payload.model_dump(
            mode="json",
        )).status_code == 404
    assert service.read(trip.id).revision == 2


"""撤销接口测试函数：本人成功恢复且重试原样返回，私有幂等字段不会进入公开JSON。"""

def test_undo_route_restores_and_returns_public_snapshot(store_engine: Engine) -> None:
    owner = AuthService(store_engine).create_user("undo-owner", "test-pass123", "user")
    trip, _, first, _, payload = save_two(store_engine, owner.id)
    settings = Settings(environment="test", database_url=SecretStr(
        store_engine.url.render_as_string(hide_password=False)))
    with TestClient(create_app(settings), headers={"X-Requested-With": "TravelMindAI"}) as client:
        assert client.post("/api/v1/auth/login", json={
            "username": "undo-owner", "password": "test-pass123",
        }).status_code == 200
        url = f"/api/v1/sessions/{trip.id}/drafts/undo"
        undone = client.post(url, json=payload.model_dump(mode="json"))
        assert undone.status_code == 200
        body = undone.json()
        assert body["response"]["result"]["extraction"] == (
            first.response.result.extraction.model_dump(mode="json")
        )
        assert "undo_request" not in body["response"]
        assert client.post(url, json=payload.model_dump(mode="json")).json() == body
        assert client.post(url, json=payload.model_dump(mode="json") | {
            "operation_id": str(uuid4()),
        }).status_code == 409


"""并发提交测试函数：两个相同双版本请求只有一个新增完整的消息与草稿。"""

def test_concurrent_plan_submission_keeps_one_complete_version(store_engine: Engine) -> None:
    trip = TripService(store_engine).create_session("并发草稿", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)

    """提交函数：把预期冲突转为结果，不吞掉其他数据库错误。"""

    def submit(_: int) -> str:
        try:
            service.append(trip.id, uuid4(), 0, sample_response(), plan=sample_plan(),
                           expected_itinerary_version=0)
            return "saved"
        except HistoryConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, range(2))) == ["conflict", "saved"]
    assert service.read(trip.id).revision == 1
    assert service.read_plan(trip.id)[0] == 1
