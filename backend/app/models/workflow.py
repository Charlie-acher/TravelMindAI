"""数据库模型层：关联会话执行与官方 PostgreSQL 检查点，删除会话时级联清理。"""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.trip import Base


class ChatWorkflow(Base):
    """会话执行类：绑定原请求、会话归属和稳定的图执行编号。"""

    __tablename__ = "chat_workflows"
    __table_args__ = (UniqueConstraint("session_id", "message_id", name="uq_workflow_message"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    message_id: Mapped[UUID]
    payload_json: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# 官方 3.1.2 表结构由 0014 显式迁移；这里只登记元数据以供 Alembic 核对。
Table("checkpoint_migrations", Base.metadata, Column("v", Integer, primary_key=True))
Table("checkpoints", Base.metadata,
      Column("thread_id", Text, ForeignKey("chat_workflows.id", ondelete="CASCADE"),
             primary_key=True, index=True),
      Column("checkpoint_ns", Text, primary_key=True, server_default=""),
      Column("checkpoint_id", Text, primary_key=True),
      Column("parent_checkpoint_id", Text), Column("type", Text),
      Column("checkpoint", JSONB, nullable=False),
      Column("metadata", JSONB, nullable=False, server_default="{}"))
Table("checkpoint_blobs", Base.metadata,
      Column("thread_id", Text, ForeignKey("chat_workflows.id", ondelete="CASCADE"),
             primary_key=True, index=True),
      Column("checkpoint_ns", Text, primary_key=True, server_default=""),
      Column("channel", Text, primary_key=True), Column("version", Text, primary_key=True),
      Column("type", Text, nullable=False), Column("blob", LargeBinary))
Table("checkpoint_writes", Base.metadata,
      Column("thread_id", Text, ForeignKey("chat_workflows.id", ondelete="CASCADE"),
             primary_key=True, index=True),
      Column("checkpoint_ns", Text, primary_key=True, server_default=""),
      Column("checkpoint_id", Text, primary_key=True), Column("task_id", Text, primary_key=True),
      Column("idx", Integer, primary_key=True), Column("channel", Text, nullable=False),
      Column("type", Text), Column("blob", LargeBinary, nullable=False),
      Column("task_path", Text, nullable=False, server_default=""))
