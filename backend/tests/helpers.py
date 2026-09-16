"""测试辅助层：集中放置多份测试共用的示例数据和离线模型替身。"""

import json
from uuid import uuid4

from app.schemas.document.base import DocumentChunk
from app.schemas.document.search import SearchHit

"""构造模型输出，null表示这轮未提到；kwargs按用例覆盖字段。"""

def answer(**changes: object) -> dict[str, object]:
    return {
        "intent": "plan_trip",
        "destination": None,
        "origin": None,
        "start_date": None,
        "end_date": None,
        "days": None,
        "travelers": None,
        "total_budget": None,
        "pace": None,
        "interests": [],
        "dietary": [],
        "lodging_preferences": [],
        "hard_constraints": [],
        "excluded_items": [],
        "assumptions": [],
    } | changes


class FakeModel:
    """顺序交出预设答案，用一次测试模拟同一用户的多轮对话。"""

    """保存答案迭代器，不连接任何远程服务。"""

    def __init__(self, answers: list[dict[str, object]]) -> None:
        self.answers = iter(answers)

    """参数与正式模型相同，因此可以通过依赖覆盖注入路由。"""

    def generate_json(self, messages: list[dict[str, str]]) -> str:
        return json.dumps(next(self.answers))


class RecordingModel:
    """按顺序交出预设答案，并记下收到的消息，方便检查修复次数及提示内容。"""

    """answers 是模型将返回的 JSON 文本；不需要配置密钥。"""

    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)
        self.calls: list[list[dict[str, str]]] = []

    """复制消息快照，避免服务后续追加消息时改变先前的调用记录。"""

    def generate_json(self, messages: list[dict[str, str]]) -> str:
        self.calls.append([message.copy() for message in messages])
        return next(self.answers)


"""证据构造函数：提供一段带真实格式编号和位置的测试原文。"""


def evidence() -> SearchHit:
    return SearchHit(
        score=0.75, file_name="杭州.md",
        chunk=DocumentChunk(
            id=uuid4(), document_id=uuid4(), order=1, section_order=1,
            text="湖滨路步行街适合步行游览。", start_char=0, end_char=14,
            section_path=["杭州", "湖滨路步行街"],
        ),
    )


