"""
数据格式层：规定资料读取结果和接口返回数据的格式。
读取服务按这些格式整理数据，前端按这些字段展示资料。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentMetadata(BaseModel):
    """资料元数据类：保存预填写或人工修改的城市和业务类别。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    city: str | None = Field(default=None, max_length=100)
    category: Literal["住宿", "景点", "餐馆"] | None = None

    """城市整理函数：去除空白和末尾的市，空值表示未标注。"""

    @field_validator("city", mode="before")
    @classmethod
    def normalize_city(cls, value: object) -> object:
        return value.strip().removesuffix("市").strip() or None if isinstance(value, str) else value

    """类别整理函数：空白表示未分类。"""

    @field_validator("category", mode="before")
    @classmethod
    def blank_category(cls, value: object) -> object:
        return value.strip() or None if isinstance(value, str) else value


class ParsedSection(BaseModel):
    """原文单元类：保存一页、一段或一个表格的文字及其原文位置。"""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)  # 当前单元的正文，不能为空。
    # PDF提供物理页码；DOCX、TXT、Markdown没有可靠页码，保持None。
    page_number: int | None = Field(default=None, ge=1)
    section_path: list[str] = Field(default_factory=list)  # 例如["杭州", "交通"]。
    order: int = Field(ge=1)  # 在原文件中的先后顺序，从1开始。


class ParsedDocument(BaseModel):
    """资料读取结果类：保存读出的全部正文和需要提醒用户的情况。"""

    sections: list[ParsedSection]  # 按原文顺序排列的阅读单元，尚未做检索切分。
    warnings: list[str] = Field(default_factory=list)  # 例如PDF中哪些页没有读到文字。


class DocumentSummary(DocumentMetadata):
    """资料摘要类：规定列表中每份资料的基本信息，不包含全文。"""

    id: UUID  # 资料编号，点击列表时用它查询详情。
    file_name: str  # 展示给用户的原文件名，不是磁盘路径。
    mime_type: str  # 文件类型，例如application/pdf。
    size_bytes: int  # 文件大小，单位为字节。
    content_hash: str  # 根据文件内容计算的指纹，用于识别重复资料。
    status: Literal["parsed", "failed", "deleting"]  # deleting已退出检索，等待完成清理。
    error_message: str | None  # 读取失败或待清理说明；成功时为None。
    section_count: int  # 读出的原文单元数量。
    warnings: list[str]  # 读取提醒，例如部分页面没有文字。
    created_at: datetime  # 资料保存时间。


class DocumentDetail(DocumentSummary):
    """资料详情类：在摘要信息上增加正文，供前端预览。"""

    sections: list[ParsedSection]  # 读取失败时为空列表，原因在error_message中。


class DocumentCityCount(BaseModel):
    """城市数量类：记录筛选后一个城市内的文件总数。"""

    city: str | None
    total: int = Field(ge=0)


class DocumentCityCounts(BaseModel):
    """城市分组类：返回所有匹配城市及文件数量。"""

    items: list[DocumentCityCount]


class DocumentCursorPage(BaseModel):
    """资料游标分页类：返回一页文件及下一页位置。"""

    items: list[DocumentSummary]
    next_cursor: str | None


class DocumentUploadResult(BaseModel):
    """上传结果类：返回资料详情，并说明这份内容是否已经保存过。"""

    document: DocumentDetail  # 新保存的资料，或之前已经保存的同一份资料。
    duplicate: bool  # True表示重复上传，数据库没有新增记录。
    processing_error: str | None = None  # 已保存但未能提交后台处理，不能误报上传失败。


class ChunkContent(BaseModel):
    """片段内容类：保存切分后的文字，以及它在已读取正文中的位置。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)
    order: int = Field(ge=1)  # 在整份资料中的片段顺序，从1开始。
    text: str = Field(min_length=1, max_length=800)  # 按Unicode字符计数，不是模型token数。
    section_order: int = Field(ge=1)  # 对应ParsedSection.order。
    page_number: int | None = Field(default=None, ge=1)  # 沿用原文页码，不推算新页码。
    section_path: list[str] = Field(default_factory=list)  # 沿用原文标题路径。
    start_char: int = Field(ge=0)  # 原文单元内的起点，从0开始，包含该字符。
    end_char: int = Field(gt=0)  # 原文单元内的终点，不包含该字符。


class DocumentChunk(ChunkContent):
    """已存片段类：在片段内容上增加固定编号，供前端和后续引用使用。"""

    id: UUID  # 保存后不再变化的片段编号。
    document_id: UUID  # 片段所属的资料编号。


class DocumentChunkPage(BaseModel):
    """片段分页类：返回片段总数和当前页，前端据此显示翻页按钮。"""

    total: int = Field(ge=0)  # 0表示这份资料还没有生成片段。
    items: list[DocumentChunk]  # 本页片段，按order升序排列。
