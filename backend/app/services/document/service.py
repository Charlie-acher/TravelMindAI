"""
资料业务服务层：检查上传文件，调用读取服务，再保存和查询资料。
由HTTP接口调用，负责衔接文件读取、磁盘保存和数据库操作。
"""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Callable, Sequence
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Engine, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import Select

from app.models.document import DocumentChunkRecord, DocumentRecord
from app.schemas.document.base import (
    DocumentChunk,
    DocumentChunkPage,
    DocumentCursorPage,
    DocumentDetail,
    DocumentMetadata,
    DocumentSummary,
    DocumentUploadResult,
    ParsedDocument,
    ParsedSection,
)
from app.services.document.chunker import DocumentChunkError, split_sections
from app.services.document.metadata import infer_metadata
from app.services.document.parser import DocumentParseError, parse_document

# 扩展名对应的文件类型；同时检查文件名和浏览器声明的类型，之后再读取实际内容。
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


"""资料转换函数：把数据库记录整理成接口需要的资料详情。"""


def document_view(row: DocumentRecord) -> DocumentDetail:
    return DocumentDetail(
        id=row.id,
        file_name=row.file_name,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        content_hash=row.content_hash,
        status="deleting" if row.status == "deleting" else (
            "parsed" if row.status == "parsed" else "failed"
        ),
        error_message=row.error_message,
        created_at=row.created_at,
        section_count=len(row.sections_json),
        warnings=row.warnings_json,
        sections=[ParsedSection.model_validate(part) for part in row.sections_json],
        city=row.city,
        category=row.category,
    )


class DocumentService:
    """资料业务服务类：按资料归属处理上传、查重、保存和读取。"""

    """初始化函数：接收数据库连接池、保存目录、资料归属和文件大小上限。"""

    def __init__(
        self,
        engine: Engine,
        upload_dir: Path,
        owner_id: str,
        max_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)
        self._upload_dir = upload_dir.resolve()
        self._owner_id = owner_id
        self._max_bytes = max_bytes

    """资料删除函数：先停用，再清理向量和原文件，最后删除档案及关联片段。"""

    def delete(
        self, document_id: UUID,
        remove_vectors: Callable[[str, UUID], None] | None = None,
    ) -> None:
        statement = select(DocumentRecord).where(
            DocumentRecord.id == document_id,
            DocumentRecord.owner_id == self._owner_id,
        ).with_for_update(nowait=True)
        try:
            with self._sessions.begin() as unit:
                row = unit.scalar(statement)
                if row is None:
                    return  # 已删除或不属于当前归属，均不触碰文件或向量。
                row.status = "deleting"
                row.error_message = "资料已停用，删除尚未完成；如有中断，请重试删除。"
            # 先提交停用状态；外部服务断开或进程退出后，新检索也不会再使用这份资料。
            with self._sessions.begin() as unit:
                row = unit.scalar(statement)
                if row is None:
                    return
                has_chunks = unit.scalar(select(DocumentChunkRecord.id).where(
                    DocumentChunkRecord.document_id == document_id,
                ).limit(1)) is not None
                if has_chunks:
                    if remove_vectors is None:
                        raise HTTPException(503, "资料已停用，请恢复向量服务配置后重试删除")
                    remove_vectors(self._owner_id, document_id)
                # 不递归删除目录；解析实际目标，拒绝异常路径或指向目录外的链接。
                path = (self._upload_dir / row.storage_name).resolve()
                if path.parent != self._upload_dir:
                    raise HTTPException(503, "资料已停用，原文件路径异常，请检查后重试删除")
                path.unlink(missing_ok=True)
                # 外键级联清理片段；数据库提交失败时，停用记录仍在，下次可重复清理。
                unit.delete(row)
        except OperationalError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise HTTPException(409, "这份资料正在处理，请稍后重试删除") from None
            raise
        except OSError:
            raise HTTPException(503, "资料已停用，原文件清理失败，请检查权限后重试删除") from None

    """筛选条件函数：城市、类别和文件名在列表与计数中使用同一规则。"""

    def _filters(
        self, query: str = "", metadata: DocumentMetadata | None = None,
        *, unclassified_city: bool = False,
    ) -> list[Any]:
        filters = [DocumentRecord.owner_id == self._owner_id]
        if metadata is not None:
            filters.extend(getattr(DocumentRecord, name) == value
                           for name, value in metadata.model_dump(exclude_none=True).items())
        if unclassified_city:
            filters.append(DocumentRecord.city.is_(None))
        if query.strip():
            # 百分号和下划线按字面查找，不当作SQL通配符。
            filters.append(DocumentRecord.file_name.icontains(query.strip(), autoescape=True))
        return filters

    """摘要查询函数：只读取列表要展示的字段，不读取正文。"""

    def _summary_query(self) -> Select[Any]:
        return select(
            DocumentRecord.id, DocumentRecord.file_name, DocumentRecord.mime_type,
            DocumentRecord.size_bytes, DocumentRecord.content_hash, DocumentRecord.status,
            DocumentRecord.error_message, DocumentRecord.created_at, DocumentRecord.city,
            DocumentRecord.category, DocumentRecord.warnings_json.label("warnings"),
            func.jsonb_array_length(DocumentRecord.sections_json).label("section_count"),
        )

    """资料列表查询函数：按资料范围筛选后，保留旧偏移分页返回格式。"""

    def list(
        self, limit: int = 50, offset: int = 0, query: str = "",
        metadata: DocumentMetadata | None = None,
    ) -> list[DocumentSummary]:
        filters = self._filters(query, metadata)
        with self._sessions() as unit:
            rows = (
                unit.execute(
                    self._summary_query()
                    .where(*filters)
                    .order_by(DocumentRecord.created_at.desc(), DocumentRecord.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .mappings()
                .all()
            )
            return [DocumentSummary.model_validate(dict(row)) for row in rows]

    """城市计数函数：对全部匹配文件分组，数量不受分页限制。"""

    def city_counts(
        self, query: str = "", city: str | None = None, category: str | None = None,
        *, unclassified_city: bool = False,
    ) -> Sequence[dict[str, Any]]:
        metadata = DocumentMetadata(city=city, category=category)
        with self._sessions() as unit:
            rows = unit.execute(select(DocumentRecord.city, func.count().label("total"))
                                .where(*self._filters(query, metadata,
                                                      unclassified_city=unclassified_city))
                                .group_by(DocumentRecord.city)
                                .order_by(DocumentRecord.city.asc().nulls_last())).mappings().all()
            return [dict(row) for row in rows]

    """资料游标分页函数：按创建时间和编号倒序，新增文件不会挤动后续页。"""

    def page(
        self, limit: int = 50, cursor: str | None = None, query: str = "",
        city: str | None = None, category: str | None = None,
        *, unclassified_city: bool = False,
    ) -> DocumentCursorPage:
        filters = self._filters(query, DocumentMetadata(city=city, category=category),
                                unclassified_city=unclassified_city)
        if cursor:
            try:
                stamp, identifier = json.loads(urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                created_at, record_id = datetime.fromisoformat(stamp), UUID(identifier)
            except (ValueError, TypeError, UnicodeDecodeError, Base64Error):
                raise HTTPException(422, "资料分页位置无效") from None
            filters.append(or_(DocumentRecord.created_at < created_at,
                               (DocumentRecord.created_at == created_at)
                               & (DocumentRecord.id < record_id)))
        with self._sessions() as unit:
            rows = unit.execute(
                self._summary_query().where(*filters)
                .order_by(DocumentRecord.created_at.desc(), DocumentRecord.id.desc())
                .limit(limit + 1)
            ).mappings().all()
        items = [DocumentSummary.model_validate(dict(row)) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            payload = json.dumps([last.created_at.isoformat(), str(last.id)]).encode()
            next_cursor = urlsafe_b64encode(payload).decode().rstrip("=")
        return DocumentCursorPage(items=items, next_cursor=next_cursor)

    """标签更新函数：锁住当前归属资料，只改显式字段，不重新解析或调用向量模型。"""

    def update_metadata(
        self, document_id: UUID, metadata: DocumentMetadata, *, fill_missing: bool = False,
    ) -> DocumentDetail:
        try:
            with self._sessions.begin() as unit:
                row = unit.scalar(select(DocumentRecord).where(
                    DocumentRecord.id == document_id,
                    DocumentRecord.owner_id == self._owner_id,
                ).with_for_update(nowait=True))
                if row is None:
                    raise HTTPException(404, "资料不存在")
                if row.status == "deleting":
                    raise HTTPException(409, "资料已停用，不能修改标签")
                for name, value in metadata.model_dump(exclude_unset=True).items():
                    if not fill_missing or getattr(row, name) is None:
                        setattr(row, name, value)
                return document_view(row)
        except OperationalError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise HTTPException(409, "这份资料正在处理，请稍后修改标签") from None
            raise

    """资料详情查询函数：按资料编号读取已保存的正文，找不到时提示资料不存在。"""

    def read(self, document_id: UUID) -> DocumentDetail:
        with self._sessions() as unit:
            row = unit.scalar(
                select(DocumentRecord).where(
                    DocumentRecord.id == document_id,
                    DocumentRecord.owner_id == self._owner_id,
                )
            )
            if row is None:
                raise HTTPException(404, "资料不存在")
            return document_view(row)

    """重新解析函数：仅重读失败资料的已存原件，成功后沿用档案编号，不重建已有片段。"""

    def retry_parse(self, document_id: UUID) -> DocumentDetail:
        try:
            with self._sessions.begin() as unit:
                row = unit.scalar(select(DocumentRecord).where(
                    DocumentRecord.id == document_id,
                    DocumentRecord.owner_id == self._owner_id,
                ).with_for_update(nowait=True))
                if row is None:
                    raise HTTPException(404, "资料不存在")
                if row.status == "deleting":
                    raise HTTPException(409, "资料已停用，请完成删除，不能重新解析")
                if row.status == "parsed":
                    return document_view(row)  # 重复请求不改动成功正文和已生成的片段编号。
                parsed = ParsedDocument(sections=[])
                failure: str | None = None
                try:
                    suffix = Path(row.storage_name).suffix.lower()
                    path = (self._upload_dir / row.storage_name).resolve()
                    if (
                        suffix not in MIME_TYPES
                        or row.storage_name != f"{row.id.hex}{suffix}"
                        or path.parent != self._upload_dir
                        or path.name != row.storage_name
                    ):
                        raise DocumentParseError("原文件路径异常，请检查后再重新解析")
                    with path.open("rb") as original:
                        content = original.read(self._max_bytes + 1)
                    # 不能修改磁盘文件来冒充原件；正文必须与上传时的内容指纹一致。
                    if (
                        len(content) > self._max_bytes or len(content) != row.size_bytes
                        or sha256(content).hexdigest() != row.content_hash
                    ):
                        raise DocumentParseError("原文件内容与上传时不一致，请重新上传正确文件")
                    parsed = parse_document(content, suffix)
                except OSError:
                    failure = "无法读取已保存的原文件，请检查文件是否存在及访问权限"
                except DocumentParseError as exc:
                    failure = str(exc)
                row.status = "failed" if failure else "parsed"
                row.error_message = failure
                row.sections_json = [part.model_dump(mode="json") for part in parsed.sections]
                row.warnings_json = parsed.warnings
                # 结果整体提交；进程中断时回滚，资料仍为失败状态，可再次手动重试。
                return document_view(row)
        except OperationalError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise HTTPException(409, "这份资料正在处理，请稍后重新解析") from None
            raise

    """片段生成函数：锁住当前资料，一次保存全部片段；已有片段时直接返回。"""

    def generate_chunks(self, document_id: UUID) -> DocumentChunkPage:
        with self._sessions.begin() as unit:
            row = unit.scalar(
                select(DocumentRecord).where(
                    DocumentRecord.id == document_id,
                    DocumentRecord.owner_id == self._owner_id,
                ).with_for_update()
            )
            if row is None:
                raise HTTPException(404, "资料不存在")
            if row.status != "parsed":
                raise HTTPException(409, "资料正文读取失败，无法生成片段")
            existing = unit.scalar(
                select(DocumentChunkRecord.id).where(
                    DocumentChunkRecord.document_id == document_id
                ).limit(1)
            )
            if existing is None:
                sections = [ParsedSection.model_validate(part) for part in row.sections_json]
                try:
                    chunks = split_sections(sections)
                except DocumentChunkError as exc:
                    raise HTTPException(422, str(exc)) from None
                unit.add_all([
                    DocumentChunkRecord(document_id=document_id, **chunk.model_dump())
                    for chunk in chunks
                ])
            # 锁和写入共用一个事务；出错则全部回滚，并发请求等提交后再检查是否已生成。
        return self.list_chunks(document_id)

    """片段列表函数：先核对资料归属，再返回总数和当前页的已存片段。"""

    def list_chunks(
        self, document_id: UUID, limit: int = 50, offset: int = 0,
    ) -> DocumentChunkPage:
        with self._sessions() as unit:
            status = unit.scalar(select(DocumentRecord.status).where(
                DocumentRecord.id == document_id,
                DocumentRecord.owner_id == self._owner_id,
            ))
            if status is None:
                raise HTTPException(404, "资料不存在")
            if status != "parsed":
                raise HTTPException(409, "资料正文读取失败，无法查看片段")
            condition = DocumentChunkRecord.document_id == document_id
            total = unit.scalar(
                select(func.count()).select_from(DocumentChunkRecord).where(condition)
            )
            # 整批片段一次提交且不可修改；数量为0时直接返回，避免生成刚完成造成前后不一致。
            if not total:
                return DocumentChunkPage(total=0, items=[])
            rows = unit.scalars(
                select(DocumentChunkRecord).where(condition)
                .order_by(DocumentChunkRecord.order).limit(limit).offset(offset)
            ).all()
            return DocumentChunkPage(
                total=total,
                items=[DocumentChunk.model_validate(row) for row in rows],
            )

    """资料上传函数：检查文件并查重，读取正文后保存原文件和读取结果。"""

    def upload(
        self, stream: BinaryIO, file_name: str, mime_type: str,
        *, uploaded_by: UUID | None = None,
    ) -> DocumentUploadResult:
        # 去掉文件名中的目录，只保留名字用于展示和判断格式。
        file_name = file_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not file_name or len(file_name) > 255 or any(ord(char) < 32 for char in file_name):
            raise HTTPException(400, "文件名为空、过长或包含控制字符")
        suffix = Path(file_name).suffix.lower()
        if suffix not in MIME_TYPES:
            raise HTTPException(415, "仅支持PDF、DOCX、TXT和Markdown文件")
        mime_type = mime_type.split(";", 1)[0].strip().lower()
        allowed_mimes = {MIME_TYPES[suffix], "", "application/octet-stream"}
        # 浏览器可能未识别文件类型，或把Markdown当普通文本；后面仍会检查实际内容。
        if suffix in {".md", ".markdown"}:
            allowed_mimes.update({"text/plain", "text/x-markdown"})
        if mime_type not in allowed_mimes:
            raise HTTPException(415, "文件类型与扩展名不符，请选择正确格式")
        # 最多多读1个字节，用实际收到的内容判断是否超过上限。
        content = stream.read(self._max_bytes + 1)
        if len(content) > self._max_bytes:
            raise HTTPException(413, f"文件超过{self._max_bytes / 1024 / 1024:g} MiB限制")
        if not content:
            raise HTTPException(400, "文件为空，请选择有内容的资料")
        fingerprint = sha256(content).hexdigest()
        with self._sessions() as unit:
            existing = unit.scalar(
                select(DocumentRecord).where(
                    DocumentRecord.owner_id == self._owner_id,
                    DocumentRecord.content_hash == fingerprint,
                )
            )
            if existing is not None:
                return DocumentUploadResult(document=document_view(existing), duplicate=True)

        # 先读文件，再开始数据库写入；读取失败也保存档案，方便用户查看原因。
        failure: str | None = None
        parsed = ParsedDocument(sections=[])
        try:
            parsed = parse_document(content, suffix)
        except DocumentParseError as exc:
            failure = str(exc)
        document_id = uuid4()
        storage_name = f"{document_id.hex}{suffix}"
        path = self._upload_dir / storage_name
        created = False
        duplicate = False
        try:
            self._upload_dir.mkdir(parents=True, exist_ok=True)
            # xb表示只创建新文件，不覆盖已有文件；磁盘文件名由程序生成。
            with path.open("xb") as target:
                created = True
                target.write(content)
            with self._sessions.begin() as unit:
                inserted_id = unit.scalar(
                    insert(DocumentRecord)
                    .values(
                        id=document_id,
                        owner_id=self._owner_id,
                        file_name=file_name,
                        storage_name=storage_name,
                        mime_type=MIME_TYPES[suffix],
                        content_hash=fingerprint,
                        size_bytes=len(content),
                        uploaded_by=uploaded_by,
                        status="failed" if failure else "parsed",
                        error_message=failure,
                        sections_json=[part.model_dump(mode="json") for part in parsed.sections],
                        warnings_json=parsed.warnings,
                        **infer_metadata(file_name, parsed.sections).model_dump(),
                    )
                    .on_conflict_do_nothing(constraint="uq_documents_owner_hash")
                    .returning(DocumentRecord.id)
                )
                # 两人同时上传同一内容时，数据库只保留先保存的记录，再返回这条记录。
                row = unit.scalar(
                    select(DocumentRecord).where(
                        DocumentRecord.owner_id == self._owner_id,
                        DocumentRecord.content_hash == fingerprint,
                    )
                )
                assert row is not None
                duplicate = inserted_id is None
                result = document_view(row)
        except Exception as exc:
            # 保存报错时清理本次新文件；若进程被强制关闭，仍可能留下未入库文件。
            if created:
                path.unlink(missing_ok=True)
            if isinstance(exc, OSError):
                raise HTTPException(503, "原文件保存失败，请检查上传目录空间和权限") from None
            raise
        if duplicate:
            path.unlink(missing_ok=True)  # 重复上传只清理本次新文件，保留已入库的原文件。
        return DocumentUploadResult(document=result, duplicate=duplicate)
