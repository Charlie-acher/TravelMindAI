"""知识库范围层：所有账号共用公共资料，旧范围迁移完成前停止资料业务。"""

from fastapi import HTTPException, Request
from sqlalchemy import select

from app.models.document import DocumentRecord

KNOWLEDGE_SCOPE = "knowledge-base"


"""范围就绪检查函数：旧资料仍在local-demo时拒绝访问，避免向量和原文不一致。"""

def require_knowledge_ready(request: Request) -> None:
    engine = request.app.state.database_engine
    if engine is None:
        raise HTTPException(503, "未启用数据库，请配置后重启服务")
    with engine.connect() as connection:
        legacy = connection.scalar(select(DocumentRecord.id).where(
            DocumentRecord.owner_id == "local-demo",
        ).limit(1))
    if legacy is not None:
        raise HTTPException(503, "知识库正在迁移，请完成旧资料范围核对后重试")
