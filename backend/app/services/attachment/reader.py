"""附件编排层：先核对全部原件归属，再读取、识别和保存可恢复结果。"""

from collections.abc import Callable
from uuid import UUID

from app.llm.client import ModelClientError
from app.llm.vision import QwenVisionClient
from app.schemas.attachment import AttachmentSnapshot
from app.services.attachment.analysis import AttachmentAnalysisError, analyze_content
from app.services.attachment.storage import AttachmentService
from app.services.chat.events import progress
from app.services.requirement.extract import ModelClient


class AttachmentReader:
    """附件读取类：缓存成功识别，失败原件保留，已提交消息由聊天层直接返回快照。"""

    """初始化方法：接收原件服务、文字模型和按需创建的视觉客户端。"""

    def __init__(
        self, storage: AttachmentService, model: ModelClient,
        vision_factory: Callable[[], QwenVisionClient] | None = None,
    ) -> None:
        self.storage = storage
        self.model = model
        self.vision_factory = vision_factory

    """读取方法：先校验整组归属，避免夹带别的会话编号时已经产生外部调用。"""

    def read(self, session_id: UUID, attachment_ids: list[UUID]) -> list[AttachmentSnapshot]:
        originals = [self.storage.get(session_id, identifier) for identifier in attachment_ids]
        snapshots = []
        for item in originals:
            if item.analysis is None:
                progress("attachment", f"正在读取附件：{item.file_name}")
                content, mime = self.storage.read_content(session_id, item.id)
                try:
                    vision = (self.vision_factory() if mime.startswith("image/")
                              and self.vision_factory is not None else None)
                    analysis = analyze_content(item.file_name, mime, content, self.model, vision)
                except (AttachmentAnalysisError, ModelClientError) as error:
                    item = self.storage.save_analysis(session_id, item.id, None, str(error)[:500])
                else:
                    item = self.storage.save_analysis(session_id, item.id, analysis, None)
            snapshots.append(AttachmentSnapshot(
                id=item.id, file_name=item.file_name, size_bytes=item.size_bytes,
                analysis=item.analysis, error_message=item.error_message,
            ))
        return snapshots


"""附件回复函数：集中说明识别内容与待确认事项，本阶段不调用规划器修改草稿。"""

def attachment_reply(items: list[AttachmentSnapshot]) -> str:
    lines = []
    for item in items:
        lines.append(f"附件：{item.file_name}")
        if item.analysis is None:
            lines.append(item.error_message or "暂未完成识别，请再次发送该附件重试。")
            continue
        result = item.analysis
        lines.append(result.summary)
        if result.city:
            lines.append(f"识别城市：{result.city}")
        for point in result.waypoints:
            label = f"{point.order}. " if point.order is not None else "顺序待确认："
            lines.append(f"{label}{point.name}（待核对）")
        lines.extend(result.warnings)
    lines.append("本次仅完成附件读取，尚未修改或创建行程；已有旅行条件和草稿已保留。")
    return "\n\n".join(lines)
