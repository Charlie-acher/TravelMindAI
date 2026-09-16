"""数据库迁移层：添加人工资料标签，现有资料保持未标注，不更改正文或向量。"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006_document_metadata"
down_revision: str | None = "0005_document_delete"
branch_labels: str | None = None
depends_on: str | None = None


"""升级函数：增加四个可空字段与审核状态约束，沿用归属索引筛选本地资料。"""

def upgrade() -> None:
    for name, length in (("city", 100), ("source", 255), ("review_status", 20), ("poi_id", 100)):
        op.add_column("documents", sa.Column(name, sa.String(length), nullable=True))
    op.create_check_constraint("ck_documents_review_status", "documents",
                               "review_status IN ('pending', 'approved', 'rejected')")


"""回滚函数：移除元数据字段和约束，正文及片段保持原样。"""

def downgrade() -> None:
    op.drop_constraint("ck_documents_review_status", "documents", type_="check")
    for name in ("poi_id", "review_status", "source", "city"):
        op.drop_column("documents", name)
