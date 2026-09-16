"""第三版迁移：新增独立资料档案表，不改动M1/M2既有业务表和数据。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_documents"
down_revision: str | None = "0002_requirement_turns"
branch_labels: str | None = None
depends_on: str | None = None


"""显式建立字段、状态/结果约束和owner索引；应用启动不会偷偷执行此函数。"""


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(100), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("storage_name", sa.String(50), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("sections_json", postgresql.JSONB(), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_name"),
        sa.UniqueConstraint("owner_id", "content_hash", name="uq_documents_owner_hash"),
        sa.CheckConstraint("size_bytes > 0", name="ck_documents_size"),
        sa.CheckConstraint("status IN ('parsed', 'failed')", name="ck_documents_status"),
        sa.CheckConstraint("jsonb_typeof(sections_json) = 'array'", name="ck_documents_sections"),
        sa.CheckConstraint("jsonb_typeof(warnings_json) = 'array'", name="ck_documents_warnings"),
        sa.CheckConstraint(
            "(status = 'parsed' AND error_message IS NULL"
            " AND jsonb_array_length(sections_json) > 0)"
            " OR (status = 'failed' AND error_message IS NOT NULL"
            " AND jsonb_array_length(sections_json) = 0)",
            name="ck_documents_result",
        ),
    )
    op.create_index("ix_documents_owner_created", "documents", ["owner_id", "created_at"])


"""回滚删除资料表（不删除磁盘原文件）；只在显式回滚或隔离测试中使用。"""


def downgrade() -> None:
    op.drop_index("ix_documents_owner_created", table_name="documents")
    op.drop_table("documents")
