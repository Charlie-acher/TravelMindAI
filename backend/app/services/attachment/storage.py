"""附件存储服务层：校验私人原件并按会话保存、读取，供上传接口和识别服务调用。"""

import warnings
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.attachment import ConversationAttachment
from app.models.trip import TravelSession
from app.schemas.attachment import AttachmentAnalysis, AttachmentView
from app.schemas.document.base import ParsedDocument
from app.services.document.parser import DocumentParseError, parse_document

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 30_000_000  # PDF按30MB计；图片和其他文字格式仍沿用10MiB。
MAX_IMAGE_PIXELS = 20_000_000  # 限制解码内存，同时防止小文件展开成超大图片。
MIME_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown",
}
IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}


"""上传校验函数：限量读取字节，再验证文件名、声明类型和真实内容。"""

def validate_upload(file: BinaryIO, filename: str, content_type: str) -> tuple[bytes, str, str]:
    if (not filename.strip() or len(filename) > 255
            or any(char in filename for char in '/\\:')
            or any(ord(char) < 32 or ord(char) == 127 for char in filename)):
        raise HTTPException(422, "附件文件名无效，不能包含目录或控制字符")
    suffix = Path(filename).suffix.lower()
    if suffix not in MIME_TYPES:
        raise HTTPException(422, "仅支持PNG、JPEG、WebP、PDF、DOCX、TXT和Markdown附件")
    mime = MIME_TYPES[suffix]
    accepted = {"", "application/octet-stream", mime}
    if suffix in {".md", ".markdown"}:
        accepted.add("text/plain")
    if content_type.split(";", 1)[0].strip().lower() not in accepted:
        raise HTTPException(422, "附件声明的类型与扩展名不符")
    max_bytes = MAX_PDF_BYTES if suffix == ".pdf" else MAX_ATTACHMENT_BYTES
    content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(413, "私人PDF最多30 MB，其他附件最多10 MiB")
    if not content:
        raise HTTPException(422, "不能上传空附件")
    try:
        if suffix in IMAGE_FORMATS:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as image:
                    if (image.format != IMAGE_FORMATS[suffix]
                            or image.width * image.height > MAX_IMAGE_PIXELS
                            or max(image.size) > 10000 or getattr(image, "is_animated", False)):
                        raise ValueError("图片格式或尺寸不支持")
                    image.verify()
                # verify检查封装，完整解码再检查缺失像素或截断数据。
                with Image.open(BytesIO(content)) as image:
                    image.load()
        else:
            if suffix == ".pdf" and not content.startswith(b"%PDF-"):
                raise ValueError("PDF格式不符")
            if (suffix in {".txt", ".md", ".markdown"}
                    and content.startswith((b"%PDF-", b"PK\x03\x04"))):
                raise ValueError("文本格式不符")
            if suffix == ".pdf":
                # 上传只检查封装与页数；扫描PDF留给MinerU识别，不能要求已有文字层。
                reader = PdfReader(BytesIO(content))
                if reader.is_encrypted or not 1 <= len(reader.pages) <= 500:
                    raise ValueError("PDF加密、为空或超过500页")
            else:
                parse_document(content, suffix)
    except DocumentParseError as error:
        raise HTTPException(422, str(error)) from None
    except (OSError, ValueError, SyntaxError, PyPdfError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise HTTPException(422, "附件损坏、格式不符或图片过大，请重新导出") from None
    return content, suffix, mime


"""展示转换函数：只返回公开字段，不把服务器存储名交给客户端。"""

def attachment_view(row: ConversationAttachment) -> AttachmentView:
    return AttachmentView(
        id=row.id, session_id=row.session_id, file_name=row.file_name, mime_type=row.mime_type,
        size_bytes=row.size_bytes, status=row.status,
        analysis=(AttachmentAnalysis.model_validate(row.analysis_json)
                  if row.analysis_json else None),
        error_message=row.error_message, created_at=row.created_at,
    )


"""会话检查函数：写操作锁定会话，与并发上传和删除使用相同的顺序。"""

def require_session(unit: Session, session_id: UUID, *, lock: bool = False) -> None:
    statement = select(TravelSession.id).where(TravelSession.id == session_id)
    if lock:
        statement = statement.with_for_update()
    if unit.scalar(statement) is None:
        raise HTTPException(404, "旅行会话不存在")


"""附件查询函数：编号和会话同时匹配，拒绝跨会话引用原件。"""

def find_attachment(unit: Session, session_id: UUID, attachment_id: UUID) -> ConversationAttachment:
    row = unit.scalar(select(ConversationAttachment).where(
        ConversationAttachment.session_id == session_id,
        ConversationAttachment.id == attachment_id,
    ))
    if row is None:
        raise HTTPException(404, "当前会话中不存在此附件")
    return row


class AttachmentService:
    """私人附件服务类：管理会话原件与识别状态，不创建资料、片段或向量。"""

    """初始化函数：接收数据库连接池和专属原件目录。"""

    def __init__(self, engine: Engine, upload_dir: Path) -> None:
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)
        self._upload_dir = upload_dir.resolve()

    """上传函数：先完成内容校验，再在会话短事务内去重并保存原件。"""

    def upload(self, session_id: UUID, file: BinaryIO, filename: str,
               content_type: str) -> AttachmentView:
        with self._sessions() as unit:
            require_session(unit, session_id)
        content, suffix, mime = validate_upload(file, filename, content_type)
        content_hash = sha256(content).hexdigest()
        created_path: Path | None = None
        try:
            with self._sessions.begin() as unit:
                require_session(unit, session_id, lock=True)
                existing = unit.scalar(select(ConversationAttachment).where(
                    ConversationAttachment.session_id == session_id,
                    ConversationAttachment.content_hash == content_hash,
                ))
                if existing is not None:
                    return attachment_view(existing)
                storage_name = f"{uuid4().hex}{suffix}"
                self._upload_dir.mkdir(parents=True, exist_ok=True)
                path = self._upload_dir / storage_name
                with path.open("xb") as output:
                    created_path = path
                    output.write(content)
                row = ConversationAttachment(
                    session_id=session_id, file_name=filename, storage_name=storage_name,
                    mime_type=mime, content_hash=content_hash, size_bytes=len(content),
                    status="uploaded", analysis_json=None, error_message=None,
                )
                unit.add(row)
                unit.flush()
                result = attachment_view(row)
            return result
        except BaseException:
            # 只移除本次成功新建的文件，不能误删并发上传已复用的原件。
            if created_path is not None:
                created_path.unlink(missing_ok=True)
            raise

    """详情函数：确认会话存在后返回本会话附件。"""

    def get(self, session_id: UUID, attachment_id: UUID) -> AttachmentView:
        with self._sessions() as unit:
            require_session(unit, session_id)
            return attachment_view(find_attachment(unit, session_id, attachment_id))

    """原件读取函数：短暂锁会话，避免会话删除在本次磁盘读取之间穿插。"""

    def read_content(self, session_id: UUID, attachment_id: UUID) -> tuple[bytes, str]:
        with self._sessions.begin() as unit:
            require_session(unit, session_id, lock=True)
            row = find_attachment(unit, session_id, attachment_id)
            path = (self._upload_dir / row.storage_name).resolve()
            if path.parent != self._upload_dir:
                raise HTTPException(404, "附件原件不存在")
            try:
                return path.read_bytes(), row.mime_type
            except FileNotFoundError:
                raise HTTPException(404, "附件原件不存在") from None

    """列表函数：按上传顺序列出当前会话原件。"""

    def list(self, session_id: UUID) -> list[AttachmentView]:
        with self._sessions() as unit:
            require_session(unit, session_id)
            rows = unit.scalars(select(ConversationAttachment).where(
                ConversationAttachment.session_id == session_id,
            ).order_by(ConversationAttachment.created_at, ConversationAttachment.id))
            return [attachment_view(row) for row in rows]

    """解析读取方法：按会话和引擎版本读取正文，不能跨会话借用缓存。"""

    def read_parsed(self, session_id: UUID, attachment_id: UUID,
                    parser_version: str) -> ParsedDocument | None:
        with self._sessions() as unit:
            require_session(unit, session_id)
            cached = find_attachment(unit, session_id, attachment_id).parsed_json
            if cached is None or cached.get("parser_version") != parser_version:
                return None
            return ParsedDocument.model_validate(cached["document"])

    """解析保存方法：在调用文字模型前保存完整正文，失败重试不重复执行OCR。"""

    def save_parsed(self, session_id: UUID, attachment_id: UUID,
                    parsed: ParsedDocument, parser_version: str) -> None:
        with self._sessions.begin() as unit:
            require_session(unit, session_id, lock=True)
            row = find_attachment(unit, session_id, attachment_id)
            row.parsed_json = {"parser_version": parser_version,
                               "document": parsed.model_dump(mode="json")}

    """分析保存函数：模型调用结束后一次写入结果，不在数据库事务里等待模型。"""

    def save_analysis(self, session_id: UUID, attachment_id: UUID,
                      analysis: AttachmentAnalysis | None,
                      error_message: str | None) -> AttachmentView:
        with self._sessions.begin() as unit:
            require_session(unit, session_id, lock=True)
            row = find_attachment(unit, session_id, attachment_id)
            if (analysis is None) == (not error_message):
                raise ValueError("必须提供识别结果或非空错误原因，二者不能同时提供")
            # 另一个请求已完成识别时复用它，迟到的失败不能覆盖成功结果。
            if row.analysis_json is not None and (analysis is None or
                    analysis.parser_version is None or
                    row.analysis_json.get("parser_version") == analysis.parser_version):
                return attachment_view(row)
            row.analysis_json = analysis.model_dump(mode="json") if analysis else None
            row.error_message = error_message[:500] if error_message else None
            row.status = "failed" if analysis is None else (
                "needs_confirmation" if analysis.warnings or any(
                    waypoint.needs_confirmation for waypoint in analysis.waypoints
                ) else "ready"
            )
            unit.flush()
            result = attachment_view(row)
        return result
