"""
资料读取服务层：读取PDF、Word和文本文件，整理正文、页码和标题。
由资料业务服务调用，读取结果交回业务服务保存。
"""

import re
from io import BytesIO
from zipfile import ZipFile

from docx import Document
from docx.table import Table
from pypdf import PdfReader

from app.schemas.document.base import ParsedDocument, ParsedSection

MAX_TEXT_CHARACTERS = 1_000_000  # 读取结果最多100万字符，包含重复携带的标题。
MAX_SECTIONS = 5000  # 最多保存5000个原文单元。
MAX_EXPANDED_BYTES = 50 * 1024 * 1024  # 限制解压后大小，避免小文件展开后占用过多内存。


class DocumentParseError(ValueError):
    """资料读取异常类：表示文件无法读取，保存可以展示给用户的失败原因。"""


"""表格读取函数：按顺序读取Word单元格中的文字，包括单元格里的嵌套表格。"""


def table_text(table: Table) -> str:
    rows: list[str] = []
    seen_cells: set[object] = set()
    character_count = 0
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            # 合并后的单元格可能被多次访问；用底层节点_tc识别，保证同一格只读一次。
            if cell._tc in seen_cells:
                continue
            seen_cells.add(cell._tc)
            parts: list[str] = []
            for block in cell.iter_inner_content():
                part = table_text(block) if isinstance(block, Table) else block.text
                character_count += len(part) + 1  # 把分隔符也计入大小，拼接前先检查上限。
                if character_count > MAX_TEXT_CHARACTERS:
                    raise DocumentParseError("表格读取结果超过100万字符，请拆分上传")
                parts.append(part)
            cells.append("\n".join(part for part in parts if part.strip()))
        if cells:
            rows.append("\t".join(cells))
    return "\n".join(rows)


"""标题更新函数：记录当前各级标题，替换已经结束的同级和下级标题。"""


def update_headings(headings: dict[int, str], level: int, text: str) -> list[str]:
    for key in list(headings):
        if key >= level:
            del headings[key]
    headings[level] = text
    return list(headings.values())


"""文本解码函数：把文件字节转换为可读文字，并统一换行符。"""


def decode_text(content: bytes) -> str:
    # 先尝试UTF-8和常见中文编码；有UTF-16文件头标记时，按标记读取。
    encodings = ["utf-8-sig", "gb18030"]
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ["utf-16"]
    for encoding in encodings:
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        # 拒绝正文里的二进制控制字符，保留正常的换行和制表符。
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            raise DocumentParseError("文件含二进制控制字符，请上传纯文本文件")
        return text.replace("\r\n", "\n").replace("\r", "\n")
    raise DocumentParseError("无法识别文本编码，请另存为UTF-8后上传")


"""资料读取函数：按文件格式选择读取方式，返回正文、原文位置和读取提醒。"""


def parse_document(content: bytes, suffix: str) -> ParsedDocument:
    sections: list[ParsedSection] = []
    warnings: list[str] = []
    headings: dict[int, str] = {}
    character_count = 0

    """正文添加函数：去掉首尾空白，检查大小后，将文字和位置加入读取结果。"""

    def append(text: str, page: int | None = None) -> None:
        nonlocal character_count
        text = text.strip()
        if not text:
            return
        if "\x00" in text:
            raise DocumentParseError("正文含无法存储的空字符，请重新导出文件")
        # 每段都携带标题路径，必须计入重复保存/返回的字符，不能只统计正文。
        character_count += len(text) + sum(len(title) for title in headings.values())
        if character_count > MAX_TEXT_CHARACTERS or len(sections) >= MAX_SECTIONS:
            raise DocumentParseError("读取结果（含标题路径）超过100万字符或5000段，请拆分上传")
        sections.append(
            ParsedSection(
                text=text,
                page_number=page,
                section_path=list(headings.values()),
                order=len(sections) + 1,
            )
        )

    try:
        if suffix == ".pdf":
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise DocumentParseError("暂不支持加密PDF，请先导出无密码版本")
            if len(reader.pages) > 500:
                raise DocumentParseError("PDF超过500页，请拆分后上传")
            blank_pages = []
            for number, page in enumerate(reader.pages, start=1):
                contents = page.get_contents()
                if contents is not None and len(contents.get_data()) > MAX_EXPANDED_BYTES:
                    raise DocumentParseError("PDF单页内容过大，请简化或拆分后上传")
                text = page.extract_text() or ""
                if not text.strip():
                    blank_pages.append(str(number))
                append(text, number)
            if not sections:
                raise DocumentParseError("PDF未读取到文字，可能是扫描版或空白页；暂不支持OCR")
            if blank_pages:
                warnings.append(
                    "以下页未读到文字（空白或扫描页，未执行OCR）：" + "、".join(blank_pages)
                )
        elif suffix == ".docx":
            # Word文件是压缩包，先检查解压后的大小，再打开正文。
            with ZipFile(BytesIO(content)) as archive:
                if sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED_BYTES:
                    raise DocumentParseError("DOCX解压后超过50MB，请拆分后上传")
            document = Document(BytesIO(content))
            for block in document.iter_inner_content():
                if isinstance(block, Table):
                    append(table_text(block))
                else:
                    style = block.style.name if block.style is not None else ""
                    match = re.fullmatch(r"(?:Heading|标题)\s*([1-9])", style, re.IGNORECASE)
                    if match and block.text.strip():
                        update_headings(headings, int(match[1]), block.text.strip())
                    append(block.text)
            warnings.append("DOCX读取正文段落和表格；未读取图片、文本框、页眉页脚和修订内容。")
        elif suffix in {".txt", ".md", ".markdown"}:
            text = decode_text(content)
            paragraph: list[str] = []
            # 识别#到######开头的标题；代码块里的#不能当成标题。
            fence: str | None = None
            for line in text.split("\n"):
                fence_match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
                in_code = fence is not None
                if suffix != ".txt" and fence_match:
                    marker = fence_match[1]
                    if fence is None:
                        fence = marker
                    elif marker[0] == fence[0] and len(marker) >= len(fence):
                        fence = None
                heading = (
                    re.match(r"^\s{0,3}(#{1,6})\s+(.+)$", line)
                    if suffix != ".txt" and not in_code and fence is None
                    else None
                )
                if heading or not line.strip():
                    append("\n".join(paragraph))
                    paragraph = []
                if heading:
                    update_headings(headings, len(heading[1]), heading[2].strip())
                    append(line)
                elif line.strip():
                    paragraph.append(line)
            append("\n".join(paragraph))
        else:
            raise DocumentParseError("暂不支持此文件格式")
    except DocumentParseError:
        raise
    except Exception:
        # 把读取工具的报错换成通俗提示；这里只处理读文件，不处理数据库或磁盘写入。
        raise DocumentParseError("文件损坏或内容与扩展名不符，请重新导出后上传") from None
    if not sections:
        raise DocumentParseError("文件没有可读取的正文，请确认不是空白文件")
    return ParsedDocument(sections=sections, warnings=warnings)
