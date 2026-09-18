"""
存储服务层：原子保存对话、完整需求及行程版本，处理无需模型的撤销。
"""

from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.attachment import ConversationAttachment
from app.models.requirement_turn import RequirementTurn
from app.models.trip import Itinerary, TravelRequest, TravelSession
from app.schemas.itinerary import PlanSnapshot, TravelPlan, UndoDraftRequest
from app.schemas.requirement.base import TravelRequestExtraction
from app.schemas.requirement.chat import RequirementChatResponse
from app.schemas.requirement.history import RequirementHistory, SavedRequirementTurn
from app.services.requirement.extract import build_result
from app.services.requirement.merge import LIST_FIELDS, SCALAR_FIELDS
from app.services.trip_service import SessionNotFoundError, save_draft_in_transaction


class HistoryConflictError(ValueError):
    """页面基于旧历史发送或重复使用了消息编号，需要恢复最新会话再操作。"""


"""从JSON快照生成独立DTO，离开数据库连接后仍能安全使用。"""

def turn_view(row: RequirementTurn) -> SavedRequirementTurn:
    return SavedRequirementTurn(
        message_id=row.message_id,
        revision=row.revision,
        response=RequirementChatResponse.model_validate(row.response_json),
    )


"""行程解析函数：旧预算草稿没有逐日格式，保留版本但不冒充完整行程。"""

def daily_plan(row: Itinerary | None) -> TravelPlan | None:
    if row is None or row.itinerary_json.get("format") != "daily-plan-v1":
        return None
    return TravelPlan.model_validate(row.itinerary_json)


"""最新行程函数：在调用方现有连接内读取版本，写入时须先锁定会话。"""

def latest_itinerary(unit: Session, session_id: UUID) -> Itinerary | None:
    return unit.scalar(select(Itinerary).where(Itinerary.session_id == session_id)
                       .order_by(Itinerary.version.desc()).limit(1))


"""行程差异函数：比较已保存内容，只列出实际变动的日期、安排和其他字段。"""

def plan_changes(previous: TravelPlan | None, current: TravelPlan) -> list[str]:
    if previous is None:
        return [f"生成{len(current.days)}天行程草稿"]
    changes = []
    old_days = {day.day: day for day in previous.days}
    new_days = {day.day: day for day in current.days}
    for day in sorted(old_days.keys() | new_days.keys()):
        old, new = old_days.get(day), new_days.get(day)
        if old == new:
            continue
        if old is None:
            changes.append(f"新增第{day}天安排")
        elif new is None:
            changes.append(f"移除第{day}天安排")
        else:
            changes.append(f"调整第{day}天的日期或活动安排")
    for field, label in (("title", "行程标题"), ("destination", "目的地"),
                         ("budget", "预算估算"), ("warnings", "限制说明")):
        if getattr(previous, field) != getattr(current, field):
            changes.append(f"更新{label}")
    return changes


"""草稿差异函数：比较数据库内的完整需求快照，补充行程摘要尚未表达的真实变化。"""

def draft_changes(
    unit: Session, previous: Itinerary | None, plan: TravelPlan,
    extraction: TravelRequestExtraction, *, restoring: bool = False,
) -> list[str]:
    old_plan = daily_plan(previous)
    changes = plan_changes(old_plan, plan)
    if previous is None or old_plan is None:
        return changes
    requirement = unit.get(TravelRequest, previous.request_id)
    if requirement is None:
        raise HistoryConflictError("找不到行程对应的需求，请重新读取会话")
    old = TravelRequestExtraction.model_validate(requirement.request_json)
    # 已由行程内容表达的同一变化不再重复列出；其他需求不依赖模型的changed_fields。
    covered: set[str] = set()
    if old_plan.destination != plan.destination:
        covered.add("destination")
    if old_plan.budget != plan.budget:
        covered.add("total_budget")
    if len(old_plan.days) != len(plan.days):
        covered.add("days")
    if [(day.day, day.date) for day in old_plan.days] != [
        (day.day, day.date) for day in plan.days
    ]:
        covered.update(("start_date", "end_date"))
    prefix = "恢复" if restoring else "更新"
    for field, label in (
        ("destination", "目的地"), ("origin", "出发地"),
        ("start_date", "出发日期"), ("end_date", "返回日期"), ("days", "旅行天数"),
        ("travelers", "出行人数"), ("total_budget", "总预算"), ("pace", "旅行节奏"),
        ("interests", "兴趣偏好"), ("dietary", "饮食偏好"),
        ("lodging_preferences", "住宿偏好"), ("hard_constraints", "必须遵守的条件"),
        ("excluded_items", "排除项目"), ("assumptions", "需求推导说明"),
    ):
        if field not in covered and getattr(old, field) != getattr(extraction, field):
            changes.append(f"{prefix}{label}")
    return changes


class RequirementHistoryService:
    """按会话读取和追加对话，保存结果后才向HTTP调用方报告成功。"""

    """复用应用生命周期管理的连接池，不在构造时执行查询。"""

    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    """一次查询获取有序轮次，再从同一份结果计算版本，避免版本与内容不一致。"""

    def read(self, session_id: UUID) -> RequirementHistory:
        with self._sessions() as unit:
            if unit.get(TravelSession, session_id) is None:
                raise SessionNotFoundError("旅行会话不存在")
            rows = unit.scalars(
                select(RequirementTurn)
                .where(RequirementTurn.session_id == session_id)
                .order_by(RequirementTurn.revision)
            ).all()
            turns = [turn_view(row) for row in rows]
        return RequirementHistory(
            session_id=session_id,
            revision=turns[-1].revision if turns else 0,
            turns=turns,
        )

    """当前计划读取函数：同一查询取得最新版本与内容，外部工具执行前结束连接。"""

    def read_plan(self, session_id: UUID) -> tuple[int, TravelPlan | None]:
        with self._sessions() as unit:
            if unit.get(TravelSession, session_id) is None:
                raise SessionNotFoundError("旅行会话不存在")
            row = latest_itinerary(unit, session_id)
            return (row.version if row else 0), daily_plan(row)

    """短事务追加：行锁保护版本检查，唯一消息编号保证已成功的重试只返回原结果。"""

    def append(
        self,
        session_id: UUID,
        message_id: UUID,
        expected_revision: int,
        response: RequirementChatResponse,
        *,
        plan: TravelPlan | None = None,
        expected_itinerary_version: int | None = None,
    ) -> SavedRequirementTurn:
        with self._sessions.begin() as unit:
            trip = unit.scalar(
                select(TravelSession).where(TravelSession.id == session_id).with_for_update()
            )
            if trip is None:
                raise SessionNotFoundError("旅行会话不存在")
            duplicate = unit.scalar(
                select(RequirementTurn).where(
                    RequirementTurn.session_id == session_id,
                    RequirementTurn.message_id == message_id,
                )
            )
            if duplicate is not None:
                saved = turn_view(duplicate)
                if (
                    "undo_request" in duplicate.response_json
                    or saved.response.result.original_message != response.result.original_message
                    or saved.revision != expected_revision + 1
                    or [item.id for item in saved.response.attachments]
                    != [item.id for item in response.attachments]
                ):
                    raise HistoryConflictError("消息编号已用于其他提交，请重新读取会话")
                return saved
            revision = (
                unit.scalar(
                    select(func.max(RequirementTurn.revision)).where(
                        RequirementTurn.session_id == session_id,
                    )
                )
                or 0
            )
            if revision != expected_revision:
                raise HistoryConflictError("会话已有新消息，请先恢复最新对话再发送")
            attachment_ids = [item.id for item in response.attachments]
            if attachment_ids:
                owned = list(unit.scalars(select(ConversationAttachment.id).where(
                    ConversationAttachment.session_id == session_id,
                    ConversationAttachment.id.in_(attachment_ids),
                )))
                if len(owned) != len(attachment_ids):
                    raise HistoryConflictError("附件不属于当前会话，无法保存本轮消息")
            if response.history_summary is not None:
                # 摘要由本轮模型生成，但覆盖进度只能依据已提交轮次，不能越界或倒退。
                coverage = RequirementTurn.response_json[
                    "history_summary"
                ]["covered_revision"].as_integer()
                previous_coverage = unit.scalar(select(coverage).where(
                    RequirementTurn.session_id == session_id, coverage.is_not(None),
                ).order_by(RequirementTurn.revision.desc()).limit(1))
                if not (previous_coverage or 0) <= (
                    response.history_summary.covered_revision
                ) <= revision:
                    raise HistoryConflictError("历史摘要覆盖版本无效，请重新读取会话")
            if plan is not None:
                previous = latest_itinerary(unit, session_id)
                version = previous.version if previous else 0
                if expected_itinerary_version is None or version != expected_itinerary_version:
                    raise HistoryConflictError("行程已有新版本，请先恢复最新草稿再发送")
                old_plan = daily_plan(previous)
                saved_plan = save_draft_in_transaction(
                    unit, session_id,
                    request_json=response.result.extraction.model_dump(mode="json"),
                    itinerary_json=plan.model_dump(mode="json"),
                )
                response = response.model_copy(update={"itinerary": PlanSnapshot(
                    itinerary_id=saved_plan.itinerary.id, version=saved_plan.itinerary.version,
                    previous_version=version or None,
                    operation="modify" if old_plan is not None else "create",
                    plan=plan, changes=draft_changes(
                        unit, previous, plan, response.result.extraction,
                    ),
                    can_undo=old_plan is not None,
                )})
            row = RequirementTurn(
                session_id=session_id,
                message_id=message_id,
                revision=revision + 1,
                response_json=response.model_dump(mode="json"),
            )
            unit.add(row)
            # 首次成功消息与标题一起提交，不额外调用模型；失败不修改标题。
            if revision == 0 and trip.title in {"新建对话", "旅行需求对话"}:
                trip.title = " ".join(response.result.original_message.split())[:40]
            # 与对话记录在同一事务提交，异常时不会留半条消息或占用轮次。
            trip.updated_at = func.clock_timestamp()
            unit.flush()
            result = turn_view(row)
        return result

    """撤销函数：锁定会话并核对双版本，把上版完整需求和计划复制成新的未确认草稿。"""

    def undo(
        self, session_id: UUID, payload: UndoDraftRequest, request_id: str,
    ) -> SavedRequirementTurn:
        with self._sessions.begin() as unit:
            trip = unit.scalar(select(TravelSession).where(
                TravelSession.id == session_id,
            ).with_for_update())
            if trip is None:
                raise SessionNotFoundError("旅行会话不存在")
            duplicate = unit.scalar(select(RequirementTurn).where(
                RequirementTurn.session_id == session_id,
                RequirementTurn.message_id == payload.operation_id,
            ))
            undo_request = payload.model_dump(mode="json")
            if duplicate is not None:
                if duplicate.response_json.get("undo_request") != undo_request:
                    raise HistoryConflictError("操作编号已用于其他提交，请重新读取会话")
                return turn_view(duplicate)
            latest_turn = unit.scalar(select(RequirementTurn).where(
                RequirementTurn.session_id == session_id,
            ).order_by(RequirementTurn.revision.desc()).limit(1))
            current = latest_itinerary(unit, session_id)
            if (
                latest_turn is None or current is None
                or latest_turn.revision != payload.expected_revision
                or current.version != payload.expected_itinerary_version
                or latest_turn.message_id != payload.target_message_id
            ):
                raise HistoryConflictError("会话或行程已有变化，请恢复最新对话后操作")
            target = turn_view(latest_turn).response
            conversation = target.conversation
            if conversation is None:
                # 兼容旧轮次时继续找最近显式话题，空列表表示用户已清空，不能跳过。
                topic_turn = unit.scalar(select(RequirementTurn).where(
                    RequirementTurn.session_id == session_id,
                    RequirementTurn.response_json["conversation"].astext.is_not(None),
                ).order_by(RequirementTurn.revision.desc()).limit(1))
                if topic_turn is not None:
                    conversation = turn_view(topic_turn).response.conversation
            snapshot = target.itinerary
            if (
                snapshot is None or snapshot.operation != "modify" or not snapshot.can_undo
                or snapshot.version != current.version or snapshot.itinerary_id != current.id
                or snapshot.previous_version is None
            ):
                raise HistoryConflictError("这条消息不是当前可撤销的修改")
            previous = unit.scalar(select(Itinerary).where(
                Itinerary.session_id == session_id, Itinerary.version == snapshot.previous_version,
            ))
            old_plan = daily_plan(previous)
            if previous is None or old_plan is None:
                raise HistoryConflictError("找不到可以恢复的逐日草稿")
            requirement = unit.get(TravelRequest, previous.request_id)
            if requirement is None:
                raise HistoryConflictError("找不到可以恢复的需求")
            restored = TravelRequestExtraction.model_validate(requirement.request_json)
            saved = save_draft_in_transaction(
                unit, session_id, request_json=restored.model_dump(mode="json"),
                itinerary_json=old_plan.model_dump(mode="json"),
            )
            result = build_result("撤销上一次行程修改", target.result.reference_date, restored)
            result.message_intent = "modify_trip"
            response = RequirementChatResponse(
                result=result, reply="已撤销上一次修改，恢复为新的行程草稿。",
                status="complete", request_id=request_id,
                # 撤销恢复行程需求，不把旧行程城市重新写成用户最近咨询的话题。
                conversation=conversation,
                changed_fields=[
                    field for field in (*SCALAR_FIELDS, *LIST_FIELDS)
                    if getattr(target.result.extraction, field) != getattr(restored, field)
                ],
                itinerary=PlanSnapshot(
                    itinerary_id=saved.itinerary.id, version=saved.itinerary.version,
                    previous_version=current.version, operation="undo", plan=old_plan,
                    changes=draft_changes(unit, current, old_plan, restored, restoring=True),
                    can_undo=False,
                ),
            )
            response_json = response.model_dump(mode="json")
            # 私有幂等依据留在数据库；公开响应由DTO校验，只返回约定字段。
            response_json["undo_request"] = undo_request
            row = RequirementTurn(session_id=session_id, message_id=payload.operation_id,
                                  revision=latest_turn.revision + 1, response_json=response_json)
            unit.add(row)
            trip.updated_at = func.clock_timestamp()
            unit.flush()
            saved_turn = turn_view(row)
        return saved_turn
