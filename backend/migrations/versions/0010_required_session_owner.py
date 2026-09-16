"""数据库迁移层：旧会话已显式处理后，强制所有新会话关联注册账号。"""

import sqlalchemy as sa
from alembic import op

revision = "0010_required_session_owner"
down_revision = "0009_document_categories"
branch_labels = None
depends_on = None


"""升级函数：存在未认领历史时停止迁移，禁止自动分配或自动删除。"""

def upgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM sessions WHERE user_id IS NULL)"
    )).scalar():
        raise RuntimeError("仍有未认领会话，请停在0009显式认领或经授权清理后再升级")
    op.alter_column("sessions", "user_id", nullable=False)


"""回滚函数：仅恢复过渡期的可空字段，不移除账号与接口认证。"""

def downgrade() -> None:
    op.alter_column("sessions", "user_id", nullable=True)
