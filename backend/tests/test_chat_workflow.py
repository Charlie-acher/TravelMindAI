"""测试层：验证实际 PostgreSQL 检查点、暂停恢复和有限返工。"""

from contextlib import nullcontext
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.schemas.requirement.chat import RequirementChatResponse
from app.schemas.requirement.history import SavedRequirementMessage
from app.services.requirement.extract import build_result
from app.services.requirement.history import HistoryConflictError, RequirementHistoryService
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID
from tests.test_itinerary_agent import requirements
from tests.test_workflow_review import candidate


def setup_run(engine, monkeypatch, reviews):
    from app.services.chat import workflow

    monkeypatch.setattr(workflow, "planning_reply_action", lambda *args, **kwargs: "continue",
                        raising=False)

    trip = TripService(engine).create_session("编排测试", user_id=TEST_USER_ID)
    history = RequirementHistoryService(engine)
    model = Mock()
    model.generate_json.side_effect = reviews
    generated = []

    def compute(sid, payload, model, service, request_id, *args, **kwargs):
        from datetime import date
        generated.append(payload.message)
        response = RequirementChatResponse(result=build_result(payload.message, date.today(),
            requirements()), reply="候选已生成", status="complete", changed_fields=[],
            request_id=request_id)
        return service.append(sid, payload.message_id, payload.expected_revision, response,
                              plan=candidate(), expected_itinerary_version=0)

    monkeypatch.setattr(workflow, "process_saved_message", compute)
    payload = SavedRequirementMessage(message="杭州两天", message_id=uuid4(), expected_revision=0)

    def call(request=payload):
        return workflow.run_saved_workflow(trip.id, request, model,
            RequirementHistoryService(engine), "test", lambda: nullcontext(Mock()), Mock())

    return trip, history, payload, call, generated


def test_rework_twice_then_pause_and_cancel_after_reopen(store_engine, monkeypatch):
    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch,
        ['{"decision":"revise","issues":["需要调整"]}'] * 3)
    waiting = call()
    assert len(generated) == 3
    assert waiting.response.workflow.status == "waiting"
    assert waiting.response.workflow.attempts == 3
    assert history.read_plan(trip.id) == (0, None)
    assert call() == waiting
    from app.schemas.workflow import WorkflowResume
    resumed = SavedRequirementMessage(message="保留现状", message_id=uuid4(), expected_revision=1,
        workflow_resume=WorkflowResume(run_id=waiting.response.workflow.run_id, action="cancel"))
    cancelled = call(resumed)
    assert cancelled.response.workflow.status == "cancelled"
    assert cancelled.response.result.extraction.destination is None
    assert call(resumed) == cancelled
    assert len(generated) == 3 and history.read(trip.id).revision == 2
    with pytest.raises(HistoryConflictError):
        call(resumed.model_copy(update={"workflow_resume": WorkflowResume(
            run_id=waiting.response.workflow.run_id, action="accept")}))


def test_choice_accept_reopens_graph_and_publishes_once(store_engine, monkeypatch):
    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch,
        ['{"decision":"choice","issues":[],"question":"是否采用当前安排？"}'])
    waiting = call()
    assert waiting.response.workflow.can_accept
    from app.schemas.workflow import WorkflowResume
    request = SavedRequirementMessage(message="采用当前安排", message_id=uuid4(),
        expected_revision=1, workflow_resume=WorkflowResume(
            run_id=waiting.response.workflow.run_id, action="accept"))
    saved = call(request)
    assert saved.response.itinerary.version == 1
    assert call(request) == saved
    assert len(generated) == 1 and history.read_plan(trip.id)[0] == 1


def test_review_failure_recovers_saved_candidate_without_regeneration(store_engine, monkeypatch):
    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch,
        [RuntimeError("simulate process exit during review"),
         '{"decision":"pass","issues":[]}'])
    with pytest.raises(RuntimeError, match="process exit"):
        call()
    assert history.read(trip.id).revision == 0
    assert len(generated) == 1
    saved = call()
    assert saved.response.itinerary.version == 1 and len(generated) == 1


def test_model_choice_saved_and_same_message_cannot_change_it(store_engine, monkeypatch):
    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch,
        ['{"decision":"pass","issues":[]}'])
    request = payload.model_copy(update={"selected_provider": "qwen"})
    saved = call(request)
    assert saved.response.selected_provider == "qwen"
    assert history.read(trip.id).turns[0].response.selected_provider == "qwen"
    assert call(request) == saved
    with pytest.raises(HistoryConflictError):
        call(payload)
    assert len(generated) == 1


def test_post_commit_failure_retry_does_not_repeat_side_effect(store_engine, monkeypatch):
    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch,
        ['{"decision":"pass","issues":[]}'])
    append = RequirementHistoryService.append
    failures = []

    def commit_then_exit(self, *args, **kwargs):
        result = append(self, *args, **kwargs)
        if not failures:
            failures.append(True)
            raise RuntimeError("response lost after commit")
        return result

    monkeypatch.setattr(RequirementHistoryService, "append", commit_then_exit)
    with pytest.raises(RuntimeError, match="after commit"):
        call()
    saved = call()
    assert saved.response.itinerary.version == 1
    assert len(generated) == 1 and history.read(trip.id).revision == 1


def test_stale_and_cross_session_resume_rejected(store_engine, monkeypatch):
    from app.schemas.workflow import WorkflowResume
    from app.services.chat.workflow import run_saved_workflow

    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch,
        ['{"decision":"choice","issues":[],"question":"是否采用？"}'])
    saved = call()
    resume = WorkflowResume(run_id=saved.response.workflow.run_id, action="accept")
    other = TripService(store_engine).create_session("另一个会话", user_id=TEST_USER_ID)
    request = SavedRequirementMessage(message="采用", message_id=uuid4(), expected_revision=0,
                                      workflow_resume=resume)
    with pytest.raises(HistoryConflictError):
        run_saved_workflow(other.id, request, Mock(), history, "cross", lambda: nullcontext(Mock()),
                           Mock())
    # 新普通消息使旧等待卡失效，即使客户端伪造最新版本也不能复活旧执行。
    response = saved.response.model_copy(deep=True)
    response.workflow = None
    response.result.original_message = "先聊别的"
    history.append(trip.id, uuid4(), 1, response)
    with pytest.raises(HistoryConflictError):
        call(request.model_copy(update={"expected_revision": 2}))


def test_concurrent_lock_and_session_delete_clean_checkpoints(store_engine, monkeypatch):
    import psycopg
    from sqlalchemy import text

    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch,
        ['{"decision":"pass","issues":[]}'])
    address = store_engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(address, autocommit=True) as connection:
        connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 4))", (str(trip.id),))
        with pytest.raises(HistoryConflictError, match="仍在处理"):
            call()
    assert generated == []
    call()
    TripService(store_engine).delete_session(trip.id, user_id=TEST_USER_ID)
    with store_engine.connect() as connection:
        for table in ("chat_workflows", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_checkpoint_migration_matches_metadata(store_engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.models import Base

    with store_engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_cancel_recovers_failed_continue_even_after_client_loses_pending_id(
    store_engine, monkeypatch,
):
    from app.schemas.workflow import WorkflowResume
    from app.services.chat import workflow

    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch,
        ['{"decision":"choice","issues":[],"question":"是否采用？"}'])
    waiting = call()
    run_id = waiting.response.workflow.run_id
    resume = SavedRequirementMessage(message="换种安排", message_id=uuid4(), expected_revision=1,
        workflow_resume=WorkflowResume(run_id=run_id, action="continue"))
    monkeypatch.setattr(workflow, "process_saved_message",
                        Mock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        call(resume)
    cancel = SavedRequirementMessage(message="保留现状", message_id=uuid4(), expected_revision=1,
        workflow_resume=WorkflowResume(run_id=run_id, action="cancel"))
    cancelled = call(cancel)
    assert cancelled.response.workflow.status == "cancelled"
    assert history.read_plan(trip.id) == (0, None)
    assert call(cancel) == cancelled


def test_cancel_restores_requirements_from_before_workflow(store_engine, monkeypatch):
    from datetime import date

    from app.schemas.workflow import WorkflowResume

    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch,
        ['{"decision":"choice","issues":[],"question":"是否采用？"}'])
    before = requirements().model_copy(update={"origin": "南京"})
    history.append(trip.id, uuid4(), 0, RequirementChatResponse(
        result=build_result("已有条件", date.today(), before), reply="已记下", status="complete",
        changed_fields=[], request_id="before"))
    waiting = call(payload.model_copy(update={"expected_revision": 1}))
    cancelled = call(SavedRequirementMessage(message="保留现状", message_id=uuid4(),
        expected_revision=2, workflow_resume=WorkflowResume(
            run_id=waiting.response.workflow.run_id, action="cancel")))
    assert cancelled.response.result.extraction == before


def test_attachment_clarification_without_message_intent_still_interrupts(
    store_engine, monkeypatch,
):
    from datetime import date

    from app.schemas.attachment import AttachmentUse
    from app.services.chat import workflow

    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch, [])

    def attachment_reply(sid, request, model, service, request_id, *args, **kwargs):
        response = RequirementChatResponse(result=build_result(request.message, date.today(),
            requirements()), reply="需要补充准确地点", status="needs_clarification",
            changed_fields=[], request_id=request_id,
            attachment_use=AttachmentUse(mode="required", apply_to_plan=True))
        assert response.result.message_intent is None
        return service.append(sid, request.message_id, request.expected_revision, response)

    monkeypatch.setattr(workflow, "process_saved_message", attachment_reply)
    result = call()
    assert result.response.workflow.status == "waiting"


"""自然结束回归：不再生成候选，不创建行程，也不重复索要条件。"""

def test_natural_ending_cancels_waiting_without_generating(store_engine, monkeypatch):
    from app.schemas.workflow import WorkflowResume
    from app.services.chat import workflow

    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch,
        ['{"decision":"choice","issues":[],"question":"是否采用？"}'])
    waiting = call()
    assert "保留现状" not in waiting.response.reply
    monkeypatch.setattr(workflow, "planning_reply_action", lambda *a, **k: "cancel", raising=False)
    message = SavedRequirementMessage(message="好吧", message_id=uuid4(), expected_revision=1,
        workflow_resume=WorkflowResume(run_id=waiting.response.workflow.run_id, action="continue"))
    result = call(message)
    assert result.response.workflow.status == "cancelled"
    assert len(generated) == 1 and history.read_plan(trip.id) == (0, None)
    assert "已有行程" not in result.response.reply
    assert call(message) == result


"""暂缓后仍可用自然语言采用已审查候选，重建图和重试不会增加版本。"""

def test_defer_keeps_candidate_then_natural_accept(store_engine, monkeypatch):
    from app.schemas.workflow import WorkflowResume
    from app.services.chat import workflow

    trip, history, payload, call, generated = setup_run(store_engine, monkeypatch,
        ['{"decision":"choice","issues":[],"question":"是否采用？"}'])
    waiting = call()
    monkeypatch.setattr(workflow, "planning_reply_action", lambda *a, **k: "defer", raising=False)
    deferred = SavedRequirementMessage(message="我再想想", message_id=uuid4(), expected_revision=1,
        workflow_resume=WorkflowResume(run_id=waiting.response.workflow.run_id, action="continue"))
    result = call(deferred)
    assert result.response.workflow.status == "waiting"
    assert result.response.workflow.preview == waiting.response.workflow.preview
    assert "再问" in result.response.reply or "再安排" in result.response.reply
    assert call(deferred) == result
    monkeypatch.setattr(workflow, "planning_reply_action", lambda *a, **k: "accept", raising=False)
    accepted = SavedRequirementMessage(message="先出一版方案", message_id=uuid4(),
        expected_revision=2,
        workflow_resume=WorkflowResume(run_id=waiting.response.workflow.run_id, action="continue"))
    saved = call(accepted)
    assert saved.response.itinerary.version == 1
    assert len(generated) == 1 and call(accepted) == saved
