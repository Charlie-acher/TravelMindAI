"""
数据格式层：规定索引进度、搜索条件及带原文位置的结果。
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.document.base import DocumentChunk, DocumentMetadata


class IndexProgress(BaseModel):
    """索引进度类：只报告已经在Milvus中查到的有效片段数量。"""

    total: int  # 当前资料在PostgreSQL中的全部片段数。
    indexed: int  # 当前向量空间已保存的片段数。
    complete: bool  # total大于0且全部已保存才为True。


class SearchRequest(DocumentMetadata):
    """搜索条件类：接收一句问题，可以限定在某一份资料中查找。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=800)
    limit: int = Field(default=5, ge=1, le=10)
    document_id: UUID | None = None  # 不填时搜索当前归属全部已建索引的资料。


class SearchHit(BaseModel):
    """搜索命中类：返回相似度、文件名与数据库里的原文片段。"""

    score: float  # 余弦相似度，越大越接近；不是答案正确率。
    file_name: str
    chunk: DocumentChunk  # 带固定编号、页码、标题和字符位置。


class SearchResult(BaseModel):
    """搜索结果类：只包含原文证据，本步不让聊天模型撰写回答。"""

    items: list[SearchHit]
