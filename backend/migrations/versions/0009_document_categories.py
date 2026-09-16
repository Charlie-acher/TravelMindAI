"""数据库迁移层：为共享旅行资料增加业务类别和可追溯的上传管理员。"""

import sqlalchemy as sa
from alembic import op

revision: str = "0009_document_categories"
down_revision: str | None = "0008_accounts"
branch_labels: str | None = None
depends_on: str | None = None


"""升级函数：旧标签列与旧资料原样保留，历史上传者保持空值。"""

def upgrade() -> None:
    op.add_column("documents", sa.Column("category", sa.String(20), nullable=True))
    op.add_column("documents", sa.Column("uploaded_by", sa.Uuid(), nullable=True))
    op.create_check_constraint("ck_documents_category", "documents",
                               "category IN ('住宿', '景点', '餐馆')")
    op.create_foreign_key("fk_documents_uploaded_by_users", "documents", "users",
                          ["uploaded_by"], ["id"], ondelete="SET NULL")
    # 旧城市与新写入使用同一规范，避免杭州和杭州市被分成两个城市组。
    op.execute("UPDATE documents SET city = NULLIF(regexp_replace(trim(city), '市$', ''), '')")
    # 文件名只出现一种明确类别才补齐；综合攻略保持未分类。
    op.execute("""
        UPDATE documents SET category = CASE
          WHEN file_name LIKE '%住宿%' AND file_name NOT LIKE '%景点%'
            AND file_name NOT LIKE '%餐馆%' THEN '住宿'
          WHEN file_name LIKE '%景点%' AND file_name NOT LIKE '%住宿%'
            AND file_name NOT LIKE '%餐馆%' THEN '景点'
          WHEN file_name LIKE '%餐馆%' AND file_name NOT LIKE '%住宿%'
            AND file_name NOT LIKE '%景点%' THEN '餐馆'
          ELSE NULL END
    """)


"""回滚函数：只移除本轮新增字段，旧资料和旧标签保持原样。"""

def downgrade() -> None:
    op.drop_constraint("fk_documents_uploaded_by_users", "documents", type_="foreignkey")
    op.drop_constraint("ck_documents_category", "documents", type_="check")
    op.drop_column("documents", "uploaded_by")
    op.drop_column("documents", "category")
