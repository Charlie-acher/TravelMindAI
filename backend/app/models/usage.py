"""数据库模型层：持久保存逐次外部调用的用量和费率快照，不保存用户正文。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.trip import Base


class UsageEvent(Base):
    """调用账目类：独立于聊天成功保存，失败调用也保留，管理员按请求和日期查询。"""

    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_started", "started_at"),
                      Index("ix_usage_request", "request_id"),
                      Index("ix_usage_owner_session", "user_id", "session_id", "started_at"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    request_id: Mapped[str]
    # 审计账目在会话删除后仍保留，不设置级联外键；旧账归属未知。
    user_id: Mapped[UUID | None]
    session_id: Mapped[UUID | None]
    message_id: Mapped[UUID | None]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
