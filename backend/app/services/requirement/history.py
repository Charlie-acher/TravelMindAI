"""
对话存储服务：只负责数据库，不调用模型，不生成助手回复。
"""

from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

from app.models.requirement_turn import RequirementTurn
from app.models.trip import TravelSession
from app.schemas.requirement.chat import RequirementChatResponse
from app.schemas.requirement.history import RequirementHistory, SavedRequirementTurn
from app.services.trip_service import SessionNotFoundError


class HistoryConflictError(ValueError):
    """页面基于旧历史发送或重复使用了消息编号，需要恢复最新会话再操作。"""


"""从JSON快照生成独立DTO，离开数据库连接后仍能安全使用。"""

def turn_view(row: RequirementTurn) -> SavedRequirementTurn:
    return SavedRequirementTurn(
        message_id=row.message_id,
        revision=row.revision,
        response=RequirementChatResponse.model_validate(row.response_json),
    )


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

    """短事务追加：行锁保护版本检查，唯一消息编号保证已成功的重试只返回原结果。"""

    def append(
        self,
        session_id: UUID,
        message_id: UUID,
        expected_revision: int,
        response: RequirementChatResponse,
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
                    saved.response.result.original_message != response.result.original_message
                    or saved.revision != expected_revision + 1
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
            row = RequirementTurn(
                session_id=session_id,
                message_id=message_id,
                revision=revision + 1,
                response_json=response.model_dump(mode="json"),
            )
            unit.add(row)
            # 与对话记录在同一事务提交，异常时不会留半条消息或占用轮次。
            trip.updated_at = func.clock_timestamp()
            unit.flush()
            result = turn_view(row)
        return result
