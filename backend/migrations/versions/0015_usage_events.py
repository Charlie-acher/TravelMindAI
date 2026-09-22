"""迁移层：新增独立调用账本，失败、停止和重试的费用不依赖聊天事务成功。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_usage_events"
down_revision = "0014_chat_workflows"
branch_labels = None
depends_on = None


"""升级函数：账本不保存用户或会话外键，删除对话不抹去已发生的费用。"""

def upgrade() -> None:
    op.create_table("usage_events", sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False))
    op.create_index("ix_usage_started", "usage_events", ["started_at"])
    op.create_index("ix_usage_request", "usage_events", ["request_id"])


"""降级函数：仅移除本次费用记录表，不修改业务历史。"""

def downgrade() -> None:
    op.drop_table("usage_events")
