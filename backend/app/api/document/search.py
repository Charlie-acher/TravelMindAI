"""HTTP接口层：提供资料索引进度、分批建立索引和原文语义搜索。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.document.dependencies import get_search_service
from app.schemas.document.base import DocumentMetadata
from app.schemas.document.search import IndexProgress, SearchRequest, SearchResult
from app.services.document.search import DocumentSearchService

router = APIRouter(tags=["资料索引与搜索"])


SearchDependency = Annotated[DocumentSearchService, Depends(get_search_service)]


"""索引进度接口函数：查询已经保存的数量，刷新不会触发付费模型请求。"""


@router.get("/documents/{document_id}/index", response_model=IndexProgress)
def read_index(document_id: UUID, service: SearchDependency) -> IndexProgress:
    return service.status(document_id)


"""索引建立接口函数：每次处理最多8个缺失片段，页面可连续调用直至完成。"""


@router.post("/documents/{document_id}/index", response_model=IndexProgress)
def build_index(document_id: UUID, service: SearchDependency) -> IndexProgress:
    return service.index_batch(document_id)


"""资料搜索接口函数：将一句问题转成向量，返回有文件名和位置的原文证据。"""


@router.post("/document-search", response_model=SearchResult)
def search_documents(body: SearchRequest, service: SearchDependency) -> SearchResult:
    metadata = DocumentMetadata.model_validate(body.model_dump(include={
        "city", "source", "review_status", "poi_id",
    }))
    return service.search(body.query, body.limit, body.document_id, metadata=metadata)
