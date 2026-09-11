"""首次建表：会话、旅行需求版本、行程版本。

此文件是历史快照，不能通过导入当前 models.py 自动建表。
未来增加字段时另写迁移，保证旧版本在任何时候都能按原样重放。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# 当前迁移编号；down_revision=None 表示这是第一版，没有上一版。
revision: str = "0001_business_tables"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


# op 负责做什么操作 sa 负责字段和约束长什么样
"""升级到第一版：先创建被引用的父表，再创建带外键的子表。"""

def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),  # 会话主键，由应用生成 UUID。
        sa.Column("thread_id", sa.Uuid(), nullable=False),  # 后续 LangGraph 线程编号。
        sa.Column("title", sa.String(200), nullable=False),  # 会话标题。
        sa.Column("status", sa.String(20), server_default="active", nullable=False),  # 状态。
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id"),  # 一个执行线程只归属一个会话。
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_sessions_status"),
    )
    op.create_table(
        "travel_requests",
        sa.Column("id", sa.Uuid(), nullable=False),  # 需求快照主键。
        sa.Column("session_id", sa.Uuid(), nullable=False),  # 所属会话。
        sa.Column("version", sa.Integer(), nullable=False),  # 会话内需求版本。
        sa.Column("request_json", postgresql.JSONB(), nullable=False),  # 旅行条件快照。
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_requests_session"),
        sa.UniqueConstraint("session_id", "version", name="uq_travel_requests_session_version"),
        sa.UniqueConstraint("id", "session_id", name="uq_travel_requests_id_session"),
        sa.CheckConstraint("version >= 1", name="ck_travel_requests_version"),
        sa.CheckConstraint("jsonb_typeof(request_json) = 'object'", name="ck_travel_requests_json"),
    )
    op.create_table(
        "itineraries",
        sa.Column("id", sa.Uuid(), nullable=False),  # 行程版本主键。
        sa.Column("session_id", sa.Uuid(), nullable=False),  # 所属会话。
        sa.Column("request_id", sa.Uuid(), nullable=False),  # 依据的需求快照主键。
        sa.Column("version", sa.Integer(), nullable=False),  # 会话内行程版本。
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),  # 草稿/已确认。
        sa.Column("itinerary_json", postgresql.JSONB(), nullable=False),  # 行程内容快照。
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_itineraries_session"),
        # 两列一起校验，防止草稿关联到其他会话的需求。
        sa.ForeignKeyConstraint(
            ["request_id", "session_id"],
            ["travel_requests.id", "travel_requests.session_id"],
            name="fk_itineraries_request_session",
        ),
        sa.UniqueConstraint("session_id", "version", name="uq_itineraries_session_version"),
        sa.CheckConstraint("version >= 1", name="ck_itineraries_version"),
        sa.CheckConstraint("status IN ('draft', 'confirmed')", name="ck_itineraries_status"),
        sa.CheckConstraint("jsonb_typeof(itinerary_json) = 'object'", name="ck_itineraries_json"),
    )


"""
回到未建表状态：按子表到父表的顺序删除；这会删除表内数据。

自动验收只在临时 schema 中回滚。真实业务库已有数据后，不要随意执行 downgrade。
"""

def downgrade() -> None:
    op.drop_table("itineraries")
    op.drop_table("travel_requests")
    op.drop_table("sessions")
