"""数据库模型层：保存每份资料最近一次后台任务及可恢复状态。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.trip import Base


class DocumentJobRecord(Base):
    """资料任务记录类：保存处理类型、执行编号、已确认进度和失败原因。"""

    __tablename__ = "document_jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('parse', 'index')", name="ck_document_jobs_kind"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'paused')",
            name="ck_document_jobs_status",
        ),
        CheckConstraint("indexed >= 0 AND total >= indexed", name="ck_document_jobs_progress"),
    )
    # 每份资料只保留最近任务；删除资料时一起清理，归属仍从documents核对。
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True,
    )
    run_id: Mapped[UUID]  # 每次显式重试换编号，旧回调不能覆盖新结果。
    kind: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12))
    indexed: Mapped[int] = mapped_column(default=0, server_default="0")
    total: Mapped[int] = mapped_column(default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
