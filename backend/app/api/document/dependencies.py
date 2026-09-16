"""
HTTP依赖层：为资料接口和聊天接口准备检索连接，请求结束后关闭连接。
"""

from collections.abc import Iterator

import httpx
from fastapi import HTTPException, Request

from app.llm.embeddings import EmbeddingClient
from app.services.document.search import DocumentSearchService
from app.services.document.vector_store import MilvusStore

"""检索服务获取函数：使用应用配置和数据库，请求结束时关闭HTTP连接。"""

def get_search_service(request: Request) -> Iterator[DocumentSearchService]:
    engine = request.app.state.database_engine
    if engine is None:
        raise HTTPException(503, "未启用数据库，请配置后重启服务")
    settings = request.app.state.settings
    with httpx.Client() as http:
        yield DocumentSearchService(
            engine, MilvusStore(settings, http), EmbeddingClient(settings, http), "local-demo"
        )


