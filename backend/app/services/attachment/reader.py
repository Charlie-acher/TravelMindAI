"""附件编排层：先核对全部原件归属，再读取、识别和保存可恢复结果。"""

from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from uuid import UUID

from pypdf import PdfReader

from app.llm.client import ModelClientError
from app.llm.vision import QwenVisionClient
from app.schemas.attachment import AttachmentSnapshot
from app.services.attachment.analysis import (
    AttachmentAnalysisError,
    analyze_content,
    analyze_parsed,
    analyze_pdf_images,
    finish_analysis,
    merge_analyses,
)
from app.services.attachment.mineru import (
    MINERU_SUFFIXES,
    PARSER_VERSION,
    MinerUClient,
    MinerUError,
)
from app.services.attachment.storage import AttachmentService
from app.services.chat.events import progress
from app.services.requirement.extract import ModelClient


class AttachmentReader:
    """附件读取类：缓存成功识别，失败原件保留，已提交消息由聊天层直接返回快照。"""

    """初始化方法：接收原件服务、文字模型和按需创建的视觉客户端。"""

    def __init__(
        self, storage: AttachmentService, model: ModelClient,
        vision_factory: Callable[[], QwenVisionClient] | None = None,
        mineru: MinerUClient | None = None,
    ) -> None:
        self.storage = storage
        self.model = model
        self.vision_factory = vision_factory
        self.mineru = mineru

    """读取方法：先校验整组归属，避免夹带别的会话编号时已经产生外部调用。"""

    def read(self, session_id: UUID, attachment_ids: list[UUID]) -> list[AttachmentSnapshot]:
        originals = [self.storage.get(session_id, identifier) for identifier in attachment_ids]
        snapshots = []
        for item in originals:
            use_mineru = (self.mineru is not None
                          and Path(item.file_name).suffix.lower() in MINERU_SUFFIXES)
            if item.analysis is None or (
                use_mineru and item.analysis.parser_version != PARSER_VERSION
            ):
                progress("attachment", f"正在读取附件：{item.file_name}")
                content, mime = self.storage.read_content(session_id, item.id)
                try:
                    vision = None
                    if self.vision_factory is not None and (
                        mime.startswith("image/") or mime == "application/pdf"
                    ):
                        try:
                            vision = self.vision_factory()
                        except ModelClientError:
                            # 正常PDF仍可读文字；只有确实需要看页面时再说明缺少视觉配置。
                            if mime != "application/pdf" and not use_mineru:
                                raise
                    if use_mineru and self.mineru is not None:
                        parsed = self.storage.read_parsed(session_id, item.id, PARSER_VERSION)
                        if parsed is None:
                            pages = (len(PdfReader(BytesIO(content)).pages)
                                     if mime == "application/pdf" else None)
                            parsed = self.mineru.parse(item.file_name, content, pages)
                            self.storage.save_parsed(session_id, item.id, parsed, PARSER_VERSION)
                        analysis = analyze_parsed(parsed, self.model)
                        results = [analysis]
                        if mime.startswith("image/") and vision is not None:
                            results.append(analyze_content(
                                item.file_name, mime, content, self.model, vision))
                        image_pages = sorted({s.page_number for s in parsed.sections
                            if s.section_path == ["图片"] and s.page_number is not None})
                        if mime == "application/pdf" and image_pages:
                            if vision is None:
                                raise AttachmentAnalysisError(
                                    "PDF包含图片内容，需要配置视觉模型后补读")
                            for start in range(0, len(image_pages), 4):
                                results.append(analyze_pdf_images(
                                    content, vision, image_pages[start:start + 4]))
                        analysis = finish_analysis(merge_analyses(results), [])
                        analysis.parser_version = PARSER_VERSION
                    else:
                        analysis = analyze_content(
                            item.file_name, mime, content, self.model, vision)
                except (AttachmentAnalysisError, ModelClientError, MinerUError) as error:
                    item = self.storage.save_analysis(session_id, item.id, None, str(error)[:500])
                    if (use_mineru and item.analysis is not None
                            and item.analysis.parser_version != PARSER_VERSION):
                        # 升级失败保留数据库旧结果，本轮不能冒充已经用新引擎成功读取。
                        item = item.model_copy(update={
                            "analysis": None, "error_message": str(error)[:500]})
                else:
                    item = self.storage.save_analysis(session_id, item.id, analysis, None)
            snapshots.append(AttachmentSnapshot(
                id=item.id, file_name=item.file_name, size_bytes=item.size_bytes,
                analysis=item.analysis, error_message=item.error_message,
            ))
        return snapshots


"""附件回复函数：简短说明读取状态，地点证据和识别疑点仅供后续规划使用。"""

def attachment_reply(items: list[AttachmentSnapshot]) -> str:
    lines = []
    for item in items:
        if item.analysis is None:
            lines.append(f"暂未能读取《{item.file_name}》，请重试或换一份清晰的资料。")
            continue
        lines.append(f"已读取《{item.file_name}》，可作为行程参考。")
    lines.append("尚未修改行程，已有旅行条件和草稿已保留。")
    return "\n\n".join(lines)
