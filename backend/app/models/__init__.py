"""数据库模型层入口：集中导出模型，供数据库迁移读取表结构。"""

from app.models.requirement_turn import RequirementTurn
from app.models.trip import Base, Itinerary, TravelRequest, TravelSession

__all__ = ["Base", "Itinerary", "TravelRequest", "TravelSession", "RequirementTurn"]
