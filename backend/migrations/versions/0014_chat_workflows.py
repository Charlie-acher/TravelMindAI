"""数据库迁移层：安装官方 3.1.2 检查点结构，并关联会话生命周期。"""

from alembic import op

revision = "0014_chat_workflows"
down_revision = "0013_attachment_parsed"
branch_labels = None
depends_on = None


"""升级函数：事务内安装固定结构，不在请求中运行官方 setup 或并发建索引。"""

def upgrade() -> None:
    op.execute("""CREATE TABLE chat_workflows (
        id TEXT PRIMARY KEY,
        session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        message_id UUID NOT NULL,
        payload_json JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_workflow_message UNIQUE(session_id, message_id))""")
    op.execute("CREATE TABLE checkpoint_migrations (v INTEGER PRIMARY KEY)")
    op.execute("INSERT INTO checkpoint_migrations SELECT generate_series(0,9)")
    op.execute("""CREATE TABLE checkpoints (
        thread_id TEXT NOT NULL REFERENCES chat_workflows(id) ON DELETE CASCADE,
        checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL,
        parent_checkpoint_id TEXT, type TEXT, checkpoint JSONB NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}',
        PRIMARY KEY(thread_id, checkpoint_ns, checkpoint_id))""")
    op.execute("""CREATE TABLE checkpoint_blobs (
        thread_id TEXT NOT NULL REFERENCES chat_workflows(id) ON DELETE CASCADE,
        checkpoint_ns TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL, version TEXT NOT NULL,
        type TEXT NOT NULL, blob BYTEA,
        PRIMARY KEY(thread_id, checkpoint_ns, channel, version))""")
    op.execute("""CREATE TABLE checkpoint_writes (
        thread_id TEXT NOT NULL REFERENCES chat_workflows(id) ON DELETE CASCADE,
        checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL,
        task_id TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL,
        type TEXT, blob BYTEA NOT NULL, task_path TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(thread_id, checkpoint_ns, checkpoint_id, task_id, idx))""")
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        op.create_index(f"ix_{table}_thread_id", table, ["thread_id"])


"""回滚函数：只移除编排记录，保留已提交的聊天与行程。"""

def downgrade() -> None:
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints",
                  "checkpoint_migrations", "chat_workflows"):
        op.drop_table(table)
