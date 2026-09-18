"""资料检索业务层：协调片段、向量模型和Milvus，处理续建与原文回查。

接口层调用本服务；PostgreSQL保存正文，Milvus负责向量相似度计算。
"""

import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Engine, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.llm.embeddings import EmbeddingClient
from app.models.document import DocumentChunkRecord, DocumentRecord
from app.schemas.document.base import DocumentChunk, DocumentMetadata
from app.schemas.document.search import IndexProgress, SearchHit, SearchResult
from app.services.document.vector_store import MilvusStore

"""搜索上下文函数：只有带指代的追问才借用历史，完整新问题按自己的地点搜索。"""


def search_context(question: str, context: str) -> str:
    # ponytail: 先覆盖明确的中文追问，复杂省略句以后再接查询改写评测。
    followup = re.match(
        r"^(?:请问|请介绍|请)?\s*(?:那里|那边|这里|这些|那些|它|第[一二三123]|"
        r"还有|分别|上面|刚才|前面|附近|怎么去|怎么走|门票|票价|开放时间|多少钱)",
        question.strip(),
    )
    # 只接续完整的简短偏好回答，避免新城市问题混入旧地点。
    preference = re.fullmatch(
        r"(?:步行|走路|骑行|骑车|地铁|公交|打车|开车|自驾|坐车)?\s*"
        r"(?:\d+|[一二三四五六七八九十两半]+)\s*(?:分钟|小时|公里|千米|米)"
        r"(?:以内|左右|内|吧|都行|可以)?[。！! ]*|"
        r"(?:步行|走路|骑行|地铁|打车|开车|坐车|都可以|都行|自然风景|历史街区|城市散步)[。！! ]*",
        question.strip(),
    )
    preference_reply = (
        "助手追问：" in context and len(question) <= 80
        and re.search(r"分钟|小时|公里|千米|步行|走路|骑车|地铁|公交|打车|开车|坐车", question)
        and not re.search(r"哪些|哪里|介绍|推荐|想去|改去|换到|换成", question)
    )
    return context[:180] if followup or preference or preference_reply else ""


class DocumentSearchService:
    """资料检索服务类：按归属建立小批量索引，并将搜索编号还原成可阅读的证据。"""

    """初始化函数：借用数据库、向量库和模型客户端，不自行读取配置。"""

    def __init__(
        self, engine: Engine, vectors: MilvusStore, model: EmbeddingClient, owner_id: str
    ) -> None:
        self._sessions = sessionmaker(bind=engine)
        self._vectors = vectors
        self._model = model
        self._owner_id = owner_id

    """资料检查函数：检查归属和读取状态，写索引前同时锁住资料行。"""

    def _document(self, unit: Session, document_id: UUID, lock: bool = False) -> None:
        query = select(DocumentRecord.id, DocumentRecord.status).where(
            DocumentRecord.id == document_id,
            DocumentRecord.owner_id == self._owner_id,
        )
        if lock:
            query = query.with_for_update(nowait=True)
        try:
            row = unit.execute(query).first()
        except OperationalError as error:
            if getattr(error.orig, "sqlstate", None) == "55P03":
                raise HTTPException(409, "这份资料正在处理，请稍后刷新进度") from None
            raise
        if row is None:
            raise HTTPException(404, "资料不存在")
        if row.status != "parsed":
            raise HTTPException(409, "资料正文读取失败，无法建立或查询索引")

    """进度读取函数：按当前真实向量编号计算，不把模型调用成功误当作保存完成。"""

    def status(self, document_id: UUID) -> IndexProgress:
        with self._sessions() as unit:
            self._document(unit, document_id)
            ids = set(
                unit.scalars(
                    select(DocumentChunkRecord.id).where(
                        DocumentChunkRecord.document_id == document_id,
                    )
                )
            )
        indexed = (
            self._vectors.indexed_ids(self._owner_id, document_id)
            if self._vectors.exists()
            else set()
        )
        count = len(ids & indexed)
        return IndexProgress(
            total=len(ids), indexed=count, complete=bool(ids) and count == len(ids)
        )

    """索引建立函数：一次最多处理8段，已保存的跳过，失败后下一次从缺失部分继续。"""

    def index_batch(self, document_id: UUID) -> IndexProgress:
        with self._sessions.begin() as unit:
            # 一次只锁当前资料的一小批处理；重复点击立即冲突，不堆积收费请求。
            self._document(unit, document_id, lock=True)
            ids = list(
                unit.scalars(
                    select(DocumentChunkRecord.id)
                    .where(
                        DocumentChunkRecord.document_id == document_id,
                    )
                    .order_by(DocumentChunkRecord.order)
                )
            )
            if not ids:
                raise HTTPException(409, "请先在资料页生成片段，再建立搜索索引")
            self._vectors.ensure_collection()
            existing = self._vectors.indexed_ids(self._owner_id, document_id)
            missing = [identifier for identifier in ids if identifier not in existing][:8]
            if missing:
                rows = unit.scalars(
                    select(DocumentChunkRecord)
                    .where(
                        DocumentChunkRecord.id.in_(missing),
                    )
                    .order_by(DocumentChunkRecord.order)
                ).all()
                result = self._model.embed([row.text for row in rows])
                self._vectors.upsert(
                    self._owner_id,
                    document_id,
                    [(row.id, vector) for row, vector in zip(rows, result.vectors, strict=True)],
                )
            # 两个数据库没有联合事务；以Milvus强一致读回为准，失败保留已成功的部分。
            confirmed = self._vectors.indexed_ids(self._owner_id, document_id)
            count = len(set(ids) & confirmed)
            return IndexProgress(total=len(ids), indexed=count, complete=count == len(ids))

    """语义搜索函数：先强制归属过滤，再回数据库核对归属和原文，丢弃孤立编号。"""

    def search(
        self, query: str, limit: int, document_id: UUID | None = None,
        *, metadata: DocumentMetadata | None = None,
        include_general: bool = False,
    ) -> SearchResult:
        with self._sessions() as unit:
            if document_id is not None:
                self._document(unit, document_id)
            conditions = [DocumentRecord.owner_id == self._owner_id,
                          DocumentRecord.status == "parsed"]
            if document_id is not None:
                conditions.append(DocumentRecord.id == document_id)
            if metadata is not None:
                for name, value in metadata.model_dump(exclude_none=True).items():
                    condition = getattr(DocumentRecord, name) == value
                    # 聊天按类别找专门资料时，也保留未分类的综合攻略；管理端仍严格筛选。
                    if name == "category" and include_general:
                        condition = or_(condition, DocumentRecord.category.is_(None))
                    conditions.append(condition)
            # 先在正文库筛出合法资料；无关及已停用资料不能挤占向量库的前200个候选。
            document_ids = list(unit.scalars(select(DocumentRecord.id).where(*conditions)))
            if not document_ids:
                return SearchResult(items=[])
            if not self._vectors.exists():
                return SearchResult(items=[])
            vector = self._model.embed([query]).vectors[0]
            candidate_limit = limit * 4
            # 重复正文挤满候选时扩大范围，复用查询向量；最多检查前200个候选。
            while True:
                candidates = []
                # 每批最多500个UUID，限制单条表达式长度；每批取同样top-k再合并全局top-k。
                for offset in range(0, len(document_ids), 500):
                    candidates.extend(self._vectors.search(
                        self._owner_id, vector, candidate_limit, document_id,
                        document_ids=document_ids[offset:offset + 500],
                    ))
                candidates = sorted(candidates, key=lambda item: item[1], reverse=True)[
                    :candidate_limit
                ]
                if not candidates:
                    return SearchResult(items=[])
                statement = (
                    select(DocumentChunkRecord, DocumentRecord.file_name)
                    .join(
                        DocumentRecord,
                        DocumentRecord.id == DocumentChunkRecord.document_id,
                    )
                    .where(
                        DocumentChunkRecord.id.in_([identifier for identifier, _ in candidates]),
                        *conditions,
                    )
                )
                if document_id is not None:
                    statement = statement.where(DocumentRecord.id == document_id)
                rows = {row.id: (row, filename) for row, filename in unit.execute(statement)}
                hits: list[SearchHit] = []
                seen: set[tuple[UUID, str]] = set()
                for identifier, score in candidates:
                    if identifier not in rows:
                        continue
                    row, filename = rows[identifier]
                    text = row.text.strip()
                    if "\n" not in text and text.lstrip("# ") in row.section_path:
                        # 标题只用来找到同一节的下一段正文，返回正文自己的编号及原文位置。
                        # 不跨标题接续，避免把空标题误配到下一个景点。
                        body = unit.scalar(select(DocumentChunkRecord).where(
                            DocumentChunkRecord.document_id == row.document_id,
                            DocumentChunkRecord.order == row.order + 1,
                            DocumentChunkRecord.section_path == row.section_path,
                        ))
                        if body is None:
                            continue
                        body_text = body.text.strip()
                        if "\n" not in body_text and body_text.lstrip("# ") in body.section_path:
                            continue
                        row = body
                    key = (row.document_id, row.text)
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(
                        SearchHit(
                            score=score, file_name=filename, chunk=DocumentChunk.model_validate(row)
                        )
                    )
                    if len(hits) == limit:
                        break
                if (
                    len(hits) >= limit
                    or len(candidates) < candidate_limit
                    or candidate_limit >= 200
                ):
                    return SearchResult(items=hits)
                candidate_limit = min(candidate_limit * 2, 200)
