"""数据库模型层入口：汇总各张表的模型，让数据库迁移工具读取完整表结构。"""

from app.models.auth import AuthSession, User
from app.models.document import DocumentChunkRecord, DocumentRecord
from app.models.document_job import DocumentJobRecord
from app.models.requirement_turn import RequirementTurn
from app.models.trip import Base, Itinerary, TravelRequest, TravelSession

# 导入资料和片段模型后，迁移工具Alembic才能把这两张表也纳入结构检查。
__all__ = [
    "Base", "Itinerary", "TravelRequest", "TravelSession", "RequirementTurn", "DocumentRecord",
    "DocumentChunkRecord", "DocumentJobRecord", "User", "AuthSession",
]
