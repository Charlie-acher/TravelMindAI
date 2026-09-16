"""
资料切分服务层：把已读取正文分成较短的片段，保留原文位置，不访问数据库。
"""

from app.schemas.document.base import ChunkContent, ParsedSection

# 首版按字符控制长度；后续接入Embedding时再核对供应方的token限制。
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
MAX_CHUNK_CHARACTERS = 2_000_000  # 包含每个片段重复携带的标题，防止结果被放大。


class DocumentChunkError(ValueError):
    """片段生成异常类：切分结果超出可处理范围时，向接口提供通俗的原因。"""


"""正文切分函数：逐个原文单元切分长文字，优先在换行或句末断开。"""


def split_sections(sections: list[ParsedSection]) -> list[ChunkContent]:
    chunks: list[ChunkContent] = []
    character_count = 0
    for section in sections:
        heading_size = sum(len(title) for title in section.section_path)
        start = 0
        while start < len(section.text):
            end = min(start + CHUNK_SIZE, len(section.text))
            if end < len(section.text):
                # 只在后半段找断点，避免句子太短造成大量小片段；找不到就按长度切。
                for separators in ["\n", "。！？.!?"]:
                    boundary = max(
                        section.text.rfind(mark, start + CHUNK_SIZE // 2, end)
                        for mark in separators
                    )
                    if boundary >= 0:
                        end = boundary + 1
                        break
            character_count += end - start + heading_size
            if character_count > MAX_CHUNK_CHARACTERS:
                raise DocumentChunkError("片段总量超过200万字符，请缩短标题或拆成多个文件")
            chunks.append(ChunkContent(
                order=len(chunks) + 1,
                text=section.text[start:end],
                section_order=section.order,
                page_number=section.page_number,
                section_path=section.section_path,
                start_char=start,
                end_char=end,
            ))
            if end == len(section.text):
                break
            # 保留末尾100字作为下一段开头；不删空格和换行，位置才能准确对回原文。
            start = end - CHUNK_OVERLAP
    return chunks
