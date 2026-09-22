"""数据库迁移层：新增标题来源和会话账目归属，旧账保留未知归属。"""

import sqlalchemy as sa
from alembic import op

revision = "0016_chat_workspace"
down_revision = "0015_usage_events"
branch_labels = None
depends_on = None


"""升级函数：旧会话标题视为手动，避免后台改写历史名称。"""

def upgrade() -> None:
    op.add_column("sessions", sa.Column("title_source", sa.String(20), nullable=False,
                                       server_default="manual"))
    op.create_check_constraint("ck_sessions_title_source", "sessions",
        "title_source IN ('pending', 'generating', 'auto', 'manual', 'fallback')")
    for name in ("user_id", "session_id", "message_id"):
        op.add_column("usage_events", sa.Column(name, sa.Uuid(), nullable=True))
    op.create_index("ix_usage_owner_session", "usage_events",
                    ["user_id", "session_id", "started_at"])


"""降级函数：仅移除本次字段，独立账目和历史内容保持原样。"""

def downgrade() -> None:
    op.drop_index("ix_usage_owner_session", table_name="usage_events")
    for name in ("message_id", "session_id", "user_id"):
        op.drop_column("usage_events", name)
    op.drop_constraint("ck_sessions_title_source", "sessions", type_="check")
    op.drop_column("sessions", "title_source")
