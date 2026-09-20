"""资料检索业务层：混合文字与向量检索，处理索引续建和同节原文回查。

接口层调用本服务；PostgreSQL保存正文，Milvus负责向量相似度计算。
"""

import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Engine, and_, case, func, literal, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from app.llm.embeddings import EmbeddingClient, EmbeddingError
from app.models.document import DocumentChunkRecord, DocumentRecord
from app.schemas.document.base import DocumentChunk, DocumentMetadata
from app.schemas.document.search import DocumentContext, IndexProgress, SearchHit, SearchResult
from app.services.document.vector_store import MilvusError, MilvusStore

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

    """检索范围函数：文字、向量和原文展开共用归属、状态及元数据限制。"""

    def _conditions(
        self, document_id: UUID | None, metadata: DocumentMetadata | None,
        include_general: bool = False,
    ) -> list[ColumnElement[bool]]:
        conditions = [DocumentRecord.owner_id == self._owner_id,
                      DocumentRecord.status == "parsed"]
        if document_id is not None:
            conditions.append(DocumentRecord.id == document_id)
        if metadata is not None:
            for name, value in metadata.model_dump(exclude_none=True).items():
                condition = getattr(DocumentRecord, name) == value
                if name == "category" and include_general:
                    condition = or_(condition, DocumentRecord.category.is_(None))
                conditions.append(condition)
        return conditions

    """候选回查函数：回读合法原文，标题只接同节正文，并去掉重复片段。"""

    def _hits(
        self, unit: Session, candidates: list[tuple[UUID, float]],
        conditions: list[ColumnElement[bool]], limit: int,
    ) -> list[SearchHit]:
        statement = (select(DocumentChunkRecord, DocumentRecord.file_name)
                     .join(DocumentRecord, DocumentRecord.id == DocumentChunkRecord.document_id)
                     .where(DocumentChunkRecord.id.in_([key for key, _ in candidates]),
                            *conditions))
        rows = {row.id: (row, filename) for row, filename in unit.execute(statement)}
        hits: list[SearchHit] = []
        seen: set[tuple[UUID, str]] = set()
        for identifier, score in candidates:
            if identifier not in rows:
                continue
            row, filename = rows[identifier]
            text = row.text.strip()
            if "\n" not in text and text.lstrip("# ") in row.section_path:
                body = unit.scalar(select(DocumentChunkRecord).where(
                    DocumentChunkRecord.document_id == row.document_id,
                    DocumentChunkRecord.order == row.order + 1,
                    DocumentChunkRecord.section_path == row.section_path))
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
            hits.append(SearchHit(score=score, file_name=filename,
                                  chunk=DocumentChunk.model_validate(row)))
            if len(hits) == limit:
                break
        return hits

    """文字检索函数：中文双字片段补充完整词匹配，完整末级标题优先。"""

    def _lexical_hits(
        self, unit: Session, query: str, conditions: list[ColumnElement[bool]],
        metadata: DocumentMetadata | None,
    ) -> list[SearchHit]:
        # 城市已单独筛选，避免每段都出现的城市名占满文字候选。
        text = query.lower()
        city = metadata.city.lower() if metadata and metadata.city else ""
        # 保留苏州博物馆这样的完整专名；仅给词法拆分增加去城市前缀的版本。
        phrases = text + " " + text.replace(city, " ") if city else text
        phrases = re.sub(r"怎么|如何|哪些|什么|推荐|介绍|一下|是否|可以|安排|门票|多少钱|开放时间",
                         " ", phrases)
        words = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", phrases)
        terms = list(dict.fromkeys([
            *words,
            *(word[i:i + 2] for word in words if re.fullmatch(r"[\u4e00-\u9fff]+", word)
              for i in range(len(word) - 1)),
        ]))
        terms = [term for term in terms if term != city][:64]
        if not terms:
            return []
        body = func.lower(DocumentChunkRecord.text)
        heading = func.lower(func.coalesce(DocumentChunkRecord.section_path[-1].astext, ""))
        # 现有资料标题含“名称｜记录键”或“城市｜名称｜主题”，后缀不是地点名称。
        heading_parts = func.replace(heading, "|", "｜")
        first = func.split_part(heading_parts, "｜", 1)
        name = case((first == city, func.split_part(heading_parts, "｜", 2)), else_=first)
        exact = and_(func.length(name) >= 2, func.strpos(literal(text), name) > 0)
        matches = [or_(body.contains(term, autoescape=True),
                       heading.contains(term, autoescape=True)) for term in terms]
        count = sum((case((match, 1), else_=0) for match in matches), literal(0))
        whole = [match for term, match in zip(terms, matches, strict=True) if term in words]
        # 单个双字交集容易把岳麓山当成岳麓书院；完整词或至少两个交集才召回。
        valid = or_(exact, count >= 2, *whole)
        score = case((exact, 100 + func.length(name)), else_=0) + count / (len(terms) + 1)
        candidates = list(unit.execute(
            select(DocumentChunkRecord.id, score)
            .join(DocumentRecord, DocumentRecord.id == DocumentChunkRecord.document_id)
            .where(*conditions, valid)
            .order_by(score.desc(), DocumentChunkRecord.document_id, DocumentChunkRecord.order)
            .limit(200)).tuples())
        return self._hits(unit, list(candidates), conditions, 200)

    """混合搜索函数：按排名融合文字与语义候选，完整标题优先，统一回查原文。"""

    def search(
        self, query: str, limit: int, document_id: UUID | None = None,
        *, metadata: DocumentMetadata | None = None,
        include_general: bool = False,
    ) -> SearchResult:
        with self._sessions() as unit:
            if document_id is not None:
                self._document(unit, document_id)
            conditions = self._conditions(document_id, metadata, include_general)
            # 先在正文库筛出合法资料；无关及已停用资料不能挤占向量库的前200个候选。
            document_ids = list(unit.scalars(select(DocumentRecord.id).where(*conditions)))
            if not document_ids:
                return SearchResult(items=[])
            lexical = self._lexical_hits(unit, query, conditions, metadata)
            semantic: list[SearchHit] = []
            try:
                vector = self._model.embed([query]).vectors[0] if self._vectors.exists() else None
                candidate_limit = min(limit * 4, 200)
                while vector is not None:
                    candidates = []
                    for offset in range(0, len(document_ids), 500):
                        candidates.extend(self._vectors.search(
                            self._owner_id, vector, candidate_limit, document_id,
                            document_ids=document_ids[offset:offset + 500]))
                    candidates = sorted(candidates, key=lambda item: item[1], reverse=True)[
                        :candidate_limit]
                    semantic = self._hits(unit, candidates, conditions, limit * 4)
                    if (len(semantic) >= limit * 4 or len(candidates) < candidate_limit
                            or candidate_limit >= 200):
                        break
                    candidate_limit = min(candidate_limit * 2, 200)
            except (EmbeddingError, MilvusError):
                if not lexical:
                    raise
            # 两种分数不是同一尺度，用倒数排名融合；同分使用同名次，避免随机UUID影响排序。
            merged: dict[UUID, SearchHit] = {}
            for results, weight in ((semantic, 1.0), (lexical, 1.1)):
                rank = 0
                previous_score = None
                for index, hit in enumerate(results, 1):
                    if hit.score != previous_score:
                        rank = index
                    previous_score = hit.score
                    score = weight / (60 + rank)
                    if results is lexical and hit.score >= 100:
                        # 西湖游船比西湖更具体；完整标题长度决定点名匹配的优先级。
                        score += int(hit.score) - 100
                    old = merged.get(hit.chunk.id)
                    merged[hit.chunk.id] = hit.model_copy(update={
                        "score": score + (old.score if old else 0)})
            unique: dict[tuple[UUID, str], SearchHit] = {}
            for hit in sorted(merged.values(), key=lambda item: item.score, reverse=True):
                unique.setdefault((hit.chunk.document_id, hit.chunk.text), hit)
            return SearchResult(items=list(unique.values())[:limit])

    """同节展开函数：核对当前范围，围绕命中点最多读取五段连续同节原文。"""

    def read_context(
        self, chunk_id: UUID, *, metadata: DocumentMetadata | None = None,
    ) -> DocumentContext:
        with self._sessions() as unit:
            found = unit.execute(
                select(DocumentChunkRecord, DocumentRecord.file_name)
                .join(DocumentRecord, DocumentRecord.id == DocumentChunkRecord.document_id)
                .where(DocumentChunkRecord.id == chunk_id,
                       *self._conditions(None, metadata))).first()
            if found is None:
                raise HTTPException(404, "原文片段不存在或已不可用")
            anchor: DocumentChunkRecord = found[0]
            filename: str = found[1]
            nearby = list(unit.scalars(select(DocumentChunkRecord).where(
                DocumentChunkRecord.document_id == anchor.document_id,
                DocumentChunkRecord.order.between(anchor.order - 5, anchor.order + 5),
            ).order_by(DocumentChunkRecord.order)))
            center = next(i for i, row in enumerate(nearby) if row.id == anchor.id)
            start, end = center, center + 1
            # 无标题的PDF/TXT只展开同一页或段落，不能把整份文件当作一个章节。
            """同节判断函数：按标题路径或无标题原文单元划定连续范围。"""

            def same_section(row: DocumentChunkRecord) -> bool:
                return (row.section_path == anchor.section_path if anchor.section_path else
                        row.section_order == anchor.section_order)

            """标题判断函数：同名标题再次出现仍代表新的章节起点。"""

            def is_heading(row: DocumentChunkRecord) -> bool:
                return (row.start_char == 0 and "\n" not in row.text.strip()
                        and row.text.strip().lstrip("# ") in row.section_path)

            while start > 0 and not is_heading(nearby[start]) and same_section(nearby[start - 1]):
                start -= 1
            while (end < len(nearby) and same_section(nearby[end])
                   and not is_heading(nearby[end])):
                end += 1
            first = max(start, min(center - 2, end - 5))
            last = min(end, first + 5)
            return DocumentContext(file_name=filename,
                items=[DocumentChunk.model_validate(row) for row in nearby[first:last]],
                truncated=first > start or last < end)
