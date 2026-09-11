"""
需求对话记录：每行保存一轮完整结果，避免只保存消息却丢掉对应需求。
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.trip import Base


class RequirementTurn(Base):
    """成功完成的一轮对话；模型/校验失败不会写入这一张表。"""

    __tablename__ = "requirement_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "revision", name="uq_requirement_turns_revision"),
        UniqueConstraint("session_id", "message_id", name="uq_requirement_turns_message"),
        CheckConstraint("revision >= 1", name="ck_requirement_turns_revision"),
        CheckConstraint("jsonb_typeof(response_json) = 'object'", name="ck_requirement_turns_json"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", name="fk_requirement_turns_session")
    )
    # 浏览器为一次发送生成UUID；超时重试沿用它，防止同一句话重复入库。
    message_id: Mapped[UUID]
    # 会话内的轮数，也是客户端提交下一条时所依据的版本。
    revision: Mapped[int]
    # 原话在result.original_message里；回复、参考日期、需求快照一起提交。
    response_json: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
