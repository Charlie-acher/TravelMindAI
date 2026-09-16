"""
数据库模型层：定义资料档案表的字段、约束和索引，供资料服务保存与查询。
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.trip import Base


class DocumentRecord(Base):
    """资料记录模型类：对应documents表，每行保存一份资料的信息和读取结果。"""

    __tablename__ = "documents"
    # 数据库规则：同一用户不能重复保存同一内容，读取状态必须与正文和错误相符。
    __table_args__ = (
        UniqueConstraint("owner_id", "content_hash", name="uq_documents_owner_hash"),
        CheckConstraint("size_bytes > 0", name="ck_documents_size"),
        CheckConstraint("status IN ('parsed', 'failed', 'deleting')", name="ck_documents_status"),
        CheckConstraint("jsonb_typeof(sections_json) = 'array'", name="ck_documents_sections"),
        CheckConstraint("jsonb_typeof(warnings_json) = 'array'", name="ck_documents_warnings"),
        CheckConstraint(
            "(status = 'parsed' AND error_message IS NULL"
            " AND jsonb_array_length(sections_json) > 0)"
            " OR (status = 'failed' AND error_message IS NOT NULL"
            " AND jsonb_array_length(sections_json) = 0)"
            " OR (status = 'deleting' AND error_message IS NOT NULL)",
            name="ck_documents_result",
        ),
        Index("ix_documents_owner_created", "owner_id", "created_at"),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name="ck_documents_review_status",
        ),
        CheckConstraint("category IN ('住宿', '景点', '餐馆')", name="ck_documents_category"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)  # 自动生成的资料主键。
    # 运行时固定为knowledge-base；旧local-demo记录必须先完成部署迁移。
    owner_id: Mapped[str] = mapped_column(String(100))
    file_name: Mapped[str] = mapped_column(String(255))  # 仅展示的原文件名，已剥离目录。
    storage_name: Mapped[str] = mapped_column(String(50), unique=True)  # 磁盘文件名：UUID加扩展名。
    mime_type: Mapped[str] = mapped_column(String(100))  # 文件类型，例如application/pdf。
    content_hash: Mapped[str] = mapped_column(String(64))  # 内容指纹SHA-256，用来检查重复。
    size_bytes: Mapped[int]  # 文件大小，单位为字节。
    city: Mapped[str | None] = mapped_column(String(100))  # 人工填写的城市，未标注为空。
    category: Mapped[str | None] = mapped_column(String(20))  # 住宿、景点、餐馆，未分类为空。
    uploaded_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_documents_uploaded_by_users", ondelete="SET NULL")
    )  # 历史资料无法确定上传者时为空。
    # 旧标签只作迁移备份，不再供API、筛选或页面使用；地图证据仍保留。
    source: Mapped[str | None] = mapped_column(String(255))
    review_status: Mapped[str | None] = mapped_column(String(20))
    poi_id: Mapped[str | None] = mapped_column(String(100))
    # parsed已读取，failed读取失败，deleting已停用待清理。
    status: Mapped[str] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(String(500))  # 失败或待清理说明。
    # 保存原始读取结果；切分后的片段另存在document_chunks表。
    sections_json: Mapped[list[JsonValue]] = mapped_column(JSONB)
    warnings_json: Mapped[list[str]] = mapped_column(JSONB)  # 读取提醒，例如部分页没有文字。
    # 由数据库记录保存时间，带时区。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentChunkRecord(Base):
    """资料片段模型类：对应document_chunks表，每行保存一段文字和原文位置。"""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "order", name="uq_document_chunks_order"),
        CheckConstraint('"order" > 0 AND section_order > 0', name="ck_document_chunks_order"),
        CheckConstraint("page_number IS NULL OR page_number > 0", name="ck_document_chunks_page"),
        CheckConstraint("jsonb_typeof(section_path) = 'array'", name="ck_document_chunks_path"),
        CheckConstraint(
            "start_char >= 0 AND end_char > start_char"
            " AND end_char - start_char = char_length(text)"
            " AND char_length(text) <= 800",
            name="ck_document_chunks_text",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)  # 固定的片段编号。
    # 归属通过资料表查询，不另存一份owner；删除资料记录时级联清理片段。
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    order: Mapped[int]  # 在整份资料内的顺序，从1开始；联合唯一约束也支持按资料分页。
    text: Mapped[str] = mapped_column(Text)  # 完整片段正文，最多800个Unicode字符。
    section_order: Mapped[int]  # 片段来自第几个原文单元。
    page_number: Mapped[int | None]  # PDF物理页码，其他格式为空。
    section_path: Mapped[list[str]] = mapped_column(JSONB)  # 原文标题路径。
    start_char: Mapped[int]  # 原文单元中的起点，从0开始，包含该字符。
    end_char: Mapped[int]  # 原文单元中的终点，不包含该字符。
