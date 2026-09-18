"""数据库迁移层：只将私人PDF附件大小上限提高至30MB，保留其他类型限制。"""

from alembic import op

revision = "0012_private_pdf_size"
down_revision = "0011_conversation_attachments"
branch_labels = None
depends_on = None


"""升级函数：按已校验的文件类型应用PDF与其他附件的大小约束。"""

def upgrade() -> None:
    op.drop_constraint("ck_attachments_size", "conversation_attachments", type_="check")
    op.create_check_constraint(
        "ck_attachments_size", "conversation_attachments",
        "size_bytes > 0 AND size_bytes <= CASE WHEN mime_type = 'application/pdf'"
        " THEN 30000000 ELSE 10485760 END",
    )


"""回滚函数：恢复10MiB约束；已有大PDF时事务失败并保留数据，不静默删除。"""

def downgrade() -> None:
    op.drop_constraint("ck_attachments_size", "conversation_attachments", type_="check")
    op.create_check_constraint(
        "ck_attachments_size", "conversation_attachments",
        "size_bytes > 0 AND size_bytes <= 10485760",
    )
