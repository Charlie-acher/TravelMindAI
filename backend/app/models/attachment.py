"""数据库模型层：保存会话私有附件的原件位置和识别结果，不关联公共知识库。"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.trip import Base


class ConversationAttachment(Base):
    """私人附件模型类：每行属于一个会话，记录原件指纹与可回查的识别结果。"""

    __tablename__ = "conversation_attachments"
    __table_args__ = (
        UniqueConstraint("session_id", "content_hash", name="uq_attachments_session_hash"),
        CheckConstraint(
            "size_bytes > 0 AND size_bytes <= CASE WHEN mime_type = 'application/pdf'"
            " THEN 30000000 ELSE 10485760 END", name="ck_attachments_size",
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_attachments_hash"),
        CheckConstraint(
            "status IN ('uploaded', 'ready', 'needs_confirmation', 'failed')",
            name="ck_attachments_status",
        ),
        CheckConstraint(
            "(status = 'uploaded' AND analysis_json IS NULL AND error_message IS NULL)"
            " OR (status IN ('ready', 'needs_confirmation') AND analysis_json IS NOT NULL"
            " AND jsonb_typeof(analysis_json) = 'object' AND error_message IS NULL)"
            " OR (status = 'failed' AND analysis_json IS NULL AND error_message IS NOT NULL"
            " AND char_length(error_message) > 0)",
            name="ck_attachments_result",
        ),
        Index("ix_attachments_session_created", "session_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", name="fk_attachments_session"),
    )
    file_name: Mapped[str] = mapped_column(String(255))  # 展示文件名，不参与磁盘寻址。
    storage_name: Mapped[str] = mapped_column(String(50), unique=True)  # 服务端UUID加扩展名。
    mime_type: Mapped[str] = mapped_column(String(100))  # 由校验后的扩展名确定。
    content_hash: Mapped[str] = mapped_column(String(64))  # SHA-256，只在本会话去重。
    size_bytes: Mapped[int]  # 单位为字节，PDF最多30MB，其他附件最多10MiB。
    status: Mapped[str] = mapped_column(String(30), server_default="uploaded")
    analysis_json: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB(none_as_null=True))
    parsed_json: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB(none_as_null=True))
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
