"""数据库迁移层：新增资料后台任务表，支持进程中断后的显式重试。"""

import sqlalchemy as sa
from alembic import op

revision: str = "0007_document_jobs"
down_revision: str | None = "0006_document_metadata"
branch_labels: str | None = None
depends_on: str | None = None


"""升级函数：只增加任务表，已有资料正文和向量保持原状。"""

def upgrade() -> None:
    op.create_table(
        "document_jobs",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("indexed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint("document_id"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.CheckConstraint("kind IN ('parse', 'index')", name="ck_document_jobs_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'paused')",
            name="ck_document_jobs_status",
        ),
        sa.CheckConstraint("indexed >= 0 AND total >= indexed", name="ck_document_jobs_progress"),
    )


"""回滚函数：移除任务记录，不删除资料和已经建立的向量。"""

def downgrade() -> None:
    op.drop_table("document_jobs")
