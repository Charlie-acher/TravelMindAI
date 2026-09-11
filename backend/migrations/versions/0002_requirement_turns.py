"""第二版迁移：只新增需求对话表，原来的会话、需求和行程数据保持兼容。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_requirement_turns"
down_revision: str | None = "0001_business_tables"
branch_labels: str | None = None
depends_on: str | None = None


"""每轮结果一行；两个唯一约束分别保证顺序不重复、重试不重复。"""

def upgrade() -> None:
    op.create_table(
        "requirement_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("response_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], name="fk_requirement_turns_session"
        ),
        sa.UniqueConstraint("session_id", "revision", name="uq_requirement_turns_revision"),
        sa.UniqueConstraint("session_id", "message_id", name="uq_requirement_turns_message"),
        sa.CheckConstraint("revision >= 1", name="ck_requirement_turns_revision"),
        sa.CheckConstraint(
            "jsonb_typeof(response_json) = 'object'", name="ck_requirement_turns_json"
        ),
    )


"""仅用于明确要求的回滚；会删除对话记录，自动测试只在临时schema执行。"""

def downgrade() -> None:
    op.drop_table("requirement_turns")
