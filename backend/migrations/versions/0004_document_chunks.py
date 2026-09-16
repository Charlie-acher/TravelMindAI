"""数据库迁移层：新增资料片段表，保留已有资料，等用户按需生成片段。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_document_chunks"
down_revision: str | None = "0003_documents"
branch_labels: str | None = None
depends_on: str | None = None


"""升级函数：创建片段字段、资料外键和位置约束，不改动已保存的原文。"""


def upgrade() -> None:
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("section_order", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_path", postgresql.JSONB(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "order", name="uq_document_chunks_order"),
        sa.CheckConstraint('"order" > 0 AND section_order > 0', name="ck_document_chunks_order"),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number > 0", name="ck_document_chunks_page"
        ),
        sa.CheckConstraint("jsonb_typeof(section_path) = 'array'", name="ck_document_chunks_path"),
        sa.CheckConstraint(
            "start_char >= 0 AND end_char > start_char"
            " AND end_char - start_char = char_length(text)"
            " AND char_length(text) <= 800",
            name="ck_document_chunks_text",
        ),
    )


"""回滚函数：移除片段表，资料档案和磁盘原文件仍然保留。"""


def downgrade() -> None:
    op.drop_table("document_chunks")
