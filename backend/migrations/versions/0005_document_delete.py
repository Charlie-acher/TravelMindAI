"""数据库迁移层：允许资料先标记为待清理，删除中断时仍可识别并重试。"""

from alembic import op

revision: str = "0005_document_delete"
down_revision: str | None = "0004_document_chunks"
branch_labels: str | None = None
depends_on: str | None = None

# 原来的读取结果规则仍保留；删除中的资料可以暂留正文，直到外部清理成功。
READ_RESULT = (
    "(status = 'parsed' AND error_message IS NULL AND jsonb_array_length(sections_json) > 0)"
    " OR (status = 'failed' AND error_message IS NOT NULL"
    " AND jsonb_array_length(sections_json) = 0)"
)


"""升级函数：扩展状态及结果约束，不新增字段，不改动已有资料内容。"""

def upgrade() -> None:
    op.drop_constraint("ck_documents_status", "documents", type_="check")
    op.drop_constraint("ck_documents_result", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_status", "documents", "status IN ('parsed', 'failed', 'deleting')"
    )
    op.create_check_constraint(
        "ck_documents_result", "documents",
        READ_RESULT + " OR (status = 'deleting' AND error_message IS NOT NULL)",
    )


"""回滚函数：有待清理资料时由旧约束阻止回滚，避免把已停用资料重新开放检索。"""

def downgrade() -> None:
    op.drop_constraint("ck_documents_status", "documents", type_="check")
    op.drop_constraint("ck_documents_result", "documents", type_="check")
    op.create_check_constraint("ck_documents_status", "documents", "status IN ('parsed', 'failed')")
    op.create_check_constraint("ck_documents_result", "documents", READ_RESULT)
