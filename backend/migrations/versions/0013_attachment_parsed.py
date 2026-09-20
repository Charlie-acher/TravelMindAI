"""数据库迁移层：保存私人附件的完整解析正文与页码，供提取失败后重试。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013_attachment_parsed"
down_revision = "0012_private_pdf_size"
branch_labels = None
depends_on = None


"""升级函数：增加可空正文缓存；旧附件和历史快照不回写。"""

def upgrade() -> None:
    op.add_column("conversation_attachments", sa.Column("parsed_json", JSONB(none_as_null=True)))


"""回滚函数：移除解析缓存字段，原件与已保存的识别快照仍保留。"""

def downgrade() -> None:
    op.drop_column("conversation_attachments", "parsed_json")
