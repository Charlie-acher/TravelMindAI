"""${message}

迁移编号：${up_revision}
上一版：${down_revision | comma,n}
生成时间：${create_date}
此文件由模板生成；执行前应审查实际 DDL，尤其是删除表或列的操作。
"""

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

# Alembic 根据这些编号确定迁移先后顺序，不依靠文件名排序。
revision: str = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


"""升级到本版本；自动生成的操作需要人工审查并补充字段说明。"""

def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


"""回退到上一版本；删除数据的操作无法仅凭再次升级恢复原数据。"""

def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
