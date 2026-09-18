"""测试辅助层：集中放置多份测试共用的示例数据和离线模型替身。"""

import json
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient as StarletteTestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.schemas.document.base import DocumentChunk
from app.schemas.document.search import SearchHit

TEST_USER_ID = UUID("00000000-0000-4000-8000-000000000001")


class ToolCallingModel(GenericFakeChatModel):
    """原生工具模型替身类：复用框架假模型，只补离线工具绑定并记录实际消息。"""

    seen_messages: list = Field(default_factory=list)
    bindings: list = Field(default_factory=list)
    route_passthrough: bool = False

    """绑定函数：保存本轮真正开放的工具，不连接提供方。"""

    def bind_tools(self, tools, **kwargs):
        self.bindings.append(([tool.name for tool in tools], kwargs))
        return self

    """生成函数：记录框架传来的原生消息，核对工具结果没有被重新包装成用户消息。"""

    def _generate(self, messages, **kwargs):
        self.seen_messages.append(messages.copy())
        if (self.route_passthrough and self.bindings
                and "continue_chat" in self.bindings[-1][0]):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content="", tool_calls=[{"name": "continue_chat", "args": {}, "id": str(uuid4())}],
                response_metadata={"finish_reason": "tool_calls"},
            ))])
        return super()._generate(messages, **kwargs)


"""兼容测试数据函数：旧用例的简写只在测试里转原生调用，正式代码没有JSON行动分支。"""


def _is_understanding_call(messages: list[dict[str, str]]) -> bool:
    """统一理解识别函数：只匹配正式提示，不影响旧独立提取接口。"""

    return any("输出TurnUnderstanding JSON" in message.get("content", "")
               for message in messages)


def _understanding_fixture(value: dict[str, object], messages: list[dict[str, str]]) -> dict[str, object]:
    """旧数据适配函数：把平面需求样例包成新聊天契约。"""

    if "requirement_update" in value or "intent" not in value:
        return value
    update = value.copy()
    intent = update.get("intent")
    destination = update.get("destination")
    is_planning = intent in {"plan_trip", "modify_trip"}
    complete = all(update.get(field) is not None
                   for field in ("destination", "days", "travelers", "total_budget"))
    is_knowledge = intent in {"travel_info", "trip_question"}
    latest = messages[-1].get("content", "") if messages else ""
    # 知识样例中的地点是查询范围，不代表修改旅行目的地。
    destination_action = "set" if is_planning and destination else "keep"
    if not destination_action == "set":
        update["destination"] = None
    cities = [destination] if isinstance(destination, str) and destination else []
    topic_action = "set" if cities and (is_planning or is_knowledge) else "keep"
    return {
        "requirement_update": update,
        "destination_action": destination_action,
        "topic_action": topic_action,
        "response_mode": "plan" if is_planning and complete else "chat",
        "query_cities": cities if is_knowledge else [],
        "retrieval_query": latest if is_knowledge else "",
        "conversation": {"topic_cities": cities, "topic_places": []},
    }


def understanding(update: dict[str, object], *, response_mode: str = "chat",
                  query_cities: list[str] | None = None, retrieval_query: str = "",
                  destination_action: str = "keep", topic_action: str = "keep",
                  topic_places: list[str] | None = None) -> dict[str, object]:
    """统一理解样例函数：让地图和规划测试显式声明路由。"""

    cities = query_cities or []
    return {
        "requirement_update": update,
        "destination_action": destination_action,
        "topic_action": topic_action,
        "response_mode": response_mode,
        "query_cities": cities,
        "retrieval_query": retrieval_query,
        "conversation": {"topic_cities": cities, "topic_places": topic_places or []},
    }


def agent_model(client):
    """答案迭代函数：与需求提取共享同一份测试答案，保持多轮顺序。"""

    def responses():
        while True:
            value = json.loads(client.generate_json([]))
            calls = ([{"name": item["tool"], "args": {k: v for k, v in item.items()
                      if k != "tool"}, "id": str(uuid4())} for item in value["tools"]]
                     if "tools" in value else [{"id": str(uuid4()), "args": value,
                     "name": "submit_plan" if "days" in value else "ask_clarification"}])
            yield AIMessage(content="", tool_calls=calls,
                            response_metadata={"finish_reason": "tool_calls"})

    return ToolCallingModel(messages=responses(), route_passthrough=True)


"""旧业务测试客户端函数：显式替换身份与归属依赖，保留原有业务边界测试。

仅用于认证加入前的独立业务回归。权限验收见test_auth.py，使用真实账号、Cookie与数据库。
测试身份由store_engine种入临时schema；无数据库的错误测试只替换身份依赖。
"""

def authenticated_client(app: FastAPI, **kwargs):
    from app.api.auth import get_current_user, require_session_owner
    from app.models.auth import User

    app.dependency_overrides[get_current_user] = lambda: User(
        id=TEST_USER_ID, username="legacy-test", role="admin", is_active=True,
    )
    app.dependency_overrides[require_session_owner] = lambda: None
    headers = {"X-Requested-With": "TravelMindAI", **kwargs.pop("headers", {})}
    return StarletteTestClient(app, headers=headers, **kwargs)

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

    def __init__(self, answers: list[dict[str, object]], text_answers: list[str] | None = None) -> None:
        self.answers = iter(answers)
        self.text_answers = iter(text_answers) if text_answers is not None else None

    """参数与正式模型相同，因此可以通过依赖覆盖注入路由。"""

    def generate_json(self, messages: list[dict[str, str]]) -> str:
        value = next(self.answers)
        if _is_understanding_call(messages):
            value = _understanding_fixture(value, messages)
        return json.dumps(value)

    """自然回答函数：默认返回稳定文本，需要断言内容时由用例显式传入。"""

    def generate_text(self, messages: list[dict[str, str]]) -> str:
        return next(self.text_answers) if self.text_answers is not None else "这是测试回答。"

    """工具模型属性：让存储回归走与正式环境相同的Agent调用链。"""

    @property
    def model(self):
        return agent_model(self)


class RecordingModel:
    """按顺序交出预设答案，并记下收到的消息，方便检查修复次数及提示内容。"""

    """answers 是模型将返回的 JSON 文本；不需要配置密钥。"""

    def __init__(self, answers: list[str], text_answers: list[str] | None = None) -> None:
        self.answers = iter(answers)
        self.text_answers = iter(text_answers) if text_answers is not None else None
        self.calls: list[list[dict[str, str]]] = []
        self.text_calls: list[list[dict[str, str]]] = []

    """复制消息快照，避免服务后续追加消息时改变先前的调用记录。"""

    def generate_json(self, messages: list[dict[str, str]]) -> str:
        self.calls.append([message.copy() for message in messages])
        raw = next(self.answers)
        if not _is_understanding_call(messages):
            return raw
        value = json.loads(raw)
        return json.dumps(_understanding_fixture(value, messages), ensure_ascii=False)

    """自然回答函数：记录连续角色消息，默认给普通测试回答。"""

    def generate_text(self, messages: list[dict[str, str]]) -> str:
        self.text_calls.append([message.copy() for message in messages])
        return next(self.text_answers) if self.text_answers is not None else "这是测试回答。"

    """工具模型属性：沿用原用例数据，但交给框架传递原生工具消息。"""

    @property
    def model(self):
        return agent_model(self)


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


