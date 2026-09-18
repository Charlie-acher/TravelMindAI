"""数据库迁移层：新增私人对话附件表，原件与识别结果仅属于所在会话。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_conversation_attachments"
down_revision = "0010_required_session_owner"
branch_labels = None
depends_on = None


"""升级函数：增加会话附件表、同会话去重规则和识别结果一致性约束。"""

def upgrade() -> None:
    op.create_table(
        "conversation_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("storage_name", sa.String(50), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), server_default="uploaded", nullable=False),
        sa.Column("analysis_json", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"],
                                name="fk_attachments_session"),
        sa.UniqueConstraint("storage_name"),
        sa.UniqueConstraint("session_id", "content_hash", name="uq_attachments_session_hash"),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 10485760", name="ck_attachments_size"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_attachments_hash"),
        sa.CheckConstraint(
            "status IN ('uploaded', 'ready', 'needs_confirmation', 'failed')",
            name="ck_attachments_status",
        ),
        sa.CheckConstraint(
            "(status = 'uploaded' AND analysis_json IS NULL AND error_message IS NULL)"
            " OR (status IN ('ready', 'needs_confirmation') AND analysis_json IS NOT NULL"
            " AND jsonb_typeof(analysis_json) = 'object' AND error_message IS NULL)"
            " OR (status = 'failed' AND analysis_json IS NULL AND error_message IS NOT NULL"
            " AND char_length(error_message) > 0)",
            name="ck_attachments_result",
        ),
    )
    op.create_index("ix_attachments_session_created", "conversation_attachments",
                    ["session_id", "created_at"])


"""回滚函数：移除附件表；磁盘原件由部署者在核对备份后另行处理。"""

def downgrade() -> None:
    op.drop_table("conversation_attachments")
