"""数据库迁移层：增加账号、登录态及会话归属，旧会话等待显式认领。"""

import sqlalchemy as sa
from alembic import op

revision = "0008_accounts"
down_revision = "0007_document_jobs"
branch_labels = None
depends_on = None


"""升级函数：保留旧会话，不猜测历史归属。"""

def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(10), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_sessions_user"),
    )
    op.create_index("ix_auth_sessions_user", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.add_column("sessions", sa.Column("user_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_sessions_user", "sessions", "users", ["user_id"], ["id"])
    op.create_index("ix_sessions_user_updated", "sessions", ["user_id", "updated_at", "id"])


"""回滚函数：仅允许无账号的迁移演练，避免真实私有数据回到无认证服务。"""

def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM users)")).scalar():
        raise RuntimeError("已有账号，禁止回滚到无认证版本；请使用备份或前向修复")
    op.drop_index("ix_sessions_user_updated", table_name="sessions")
    op.drop_constraint("fk_sessions_user", "sessions", type_="foreignkey")
    op.drop_column("sessions", "user_id")
    op.drop_table("auth_sessions")
    op.drop_table("users")
