"""聊天业务层：每轮检索知识库，将有引用的回答与需求收集提示组合。

主聊天接口调用本文件，检索服务负责归属检查，问答服务负责模型与引用校验。
"""

import re

from app.schemas.document.answer import AnswerResult
from app.schemas.document.search import SearchHit
from app.schemas.requirement.chat import RequirementChatResponse
from app.schemas.requirement.history import SavedRequirementTurn
from app.services.amap import AmapClient
from app.services.document.answer import answer_from_sources
from app.services.document.search import DocumentSearchService, search_context
from app.services.requirement.extract import ModelClient
from app.services.web_search import WebSearchClient

"""近期话题整理函数：保留最近三轮原话、景点顺序和标题，帮助理解连续追问。"""


def build_chat_context(turns: list[SavedRequirementTurn]) -> str:
    parts: list[str] = []
    # 最近轮优先，引用标题放在长原话前面，避免旧问题挤掉刚换的地点。
    for index, turn in enumerate(reversed(turns[-3:]), 1):
        knowledge = turn.response.knowledge
        if knowledge is not None:
            if knowledge.clarification:
                parts.append(f"最近第{index}轮助手追问：" + knowledge.clarification)
            if knowledge.attractions:
                names = [f"{order}.{item.city}/{item.name}"
                         for order, item in enumerate(knowledge.attractions, 1)]
                parts.append(f"最近第{index}轮景点顺序（仅供指代）：" + "；".join(names))
            titles = [" / ".join(source.hit.chunk.section_path)[:160]
                      for source in knowledge.sources[:5]]
            if titles:
                parts.append(f"最近第{index}轮引用标题：" + "；".join(titles))
        parts.append(f"最近第{index}轮原话：" + turn.response.result.original_message[:800])
    return "\n".join(parts)


"""距离追问函数：常见口语距离缺少衡量方式时先问，不替用户定义“近”。"""


def proximity_clarification(message: str, context: str = "") -> str | None:
    if not re.search(r"离得(?:不远|近)|别.{0,3}太远|近一?[点些]|挨着|相距不远|距离近", message):
        return None
    mode = re.search(r"步行|走路|骑行|骑车|地铁|公交|打车|开车|自驾|坐车", message)
    limit = re.search(r"(?:\d+|[一二三四五六七八九十两半]+)\s*(?:分钟|小时|公里|千米|米)", message)
    # 只记用户原话，助手提问里的示例不能被当成用户已经接受的限制。
    if search_context(message, context):
        for line in context.splitlines():
            if "原话：" not in line:
                continue
            previous = line.split("原话：", 1)[1]
            old_mode = re.search(r"步行|走路|骑行|骑车|地铁|公交|打车|开车|自驾|坐车", previous)
            old_limit = re.search(
                r"(?:\d+|[一二三四五六七八九十两半]+)\s*(?:分钟|小时|公里|千米|米)", previous,
            )
            if old_mode and old_limit and (mode is None or mode[0] == old_mode[0]):
                mode, limit = mode or old_mode, limit or old_limit
                break
    if mode and limit:
        return None
    if mode:
        return f"你希望景点之间{mode[0]}大约多久以内？比如半小时以内，也可以说一个距离范围。"
    if limit:
        return "这个距离范围你打算步行、骑车，还是乘车游览？"
    return "可以按相互靠近的景点来筛选。你希望景点之间步行多久以内，还是可以接受坐车？"


"""聊天依据生成函数：先检索全部问题，再生成知识回答；任一服务失败直接向外报错。"""


def ground_chat_response(
    response: RequirementChatResponse,
    search: DocumentSearchService,
    model: ModelClient,
    destination: str | None = None,
    history_context: str | None = None,
    maps: AmapClient | None = None,
    web: WebSearchClient | None = None,
) -> RequirementChatResponse:
    message = response.result.original_message
    # 上轮话题仅辅助“那里/还有呢”的理解，不把旧答案当作新事实证据。
    dates = response.result.extraction
    lodging_question = any(word in message for word in ("住宿", "酒店", "民宿", "旅馆", "青旅"))
    missing_stay = [label for label, value in (
        ("入住日期", dates.start_date), ("离店日期", dates.end_date), ("入住人数", dates.travelers),
    ) if value is None]
    # 按价格筛酒店先补齐入住条件，避免模型凭经验推荐一个无法核实的预算档次。
    needs_stay = lodging_question and bool(missing_stay) and any(
        word in message for word in ("元", "预算", "房价", "价格", "便宜", "多少钱")
    )
    travel_dates = f"旅行日期：{dates.start_date}至{dates.end_date}" if dates.start_date else ""
    context = "\n".join(filter(None, [destination, travel_dates, history_context]))
    topic = search_context(message, context)
    prefix = f"对话话题：{topic}\n" if topic else ""
    # 景点推荐默认需要介绍与门票；扩展检索词不改写模型收到的用户原问题。
    suffix = "\n景点介绍 游览特色 门票" if any(
        word in message for word in ("景点", "散步", "游览", "门票")
    ) else ""
    size = 800 - len(prefix) - len(suffix)
    hits: dict[str, SearchHit] = {}
    # 6000字主聊天保持兼容，分段查询覆盖末尾；每段复用原检索的归属核对。
    for offset in range(0, len(message), size):
        for hit in search.search(prefix + message[offset:offset + size] + suffix, 10).items:
            # 标题已随正文携带，无需单独占一个事实名额；资料管理页仍可查到标题。
            text = hit.chunk.text.strip()
            if "\n" not in text and text.lstrip("# ") in hit.chunk.section_path:
                continue
            key = str(hit.chunk.id)
            if key not in hits or hit.score > hits[key].score:
                hits[key] = hit
    evidence = sorted(hits.values(), key=lambda item: item.score, reverse=True)[:5]
    clarification = proximity_clarification(message, context)
    if clarification:
        knowledge = AnswerResult(status="insufficient", points=[], sources=[],
                                 clarification=clarification)
    elif needs_stay:
        knowledge = AnswerResult(status="insufficient", points=[], sources=[])
    else:
        knowledge = answer_from_sources(
            message, evidence, model, conversation_context=context, maps=maps,
            # 只登记个人条件时不外发搜索；知识提问和混合提问才补充网页证据。
            web=web if topic or response.result.message_intent in {
                "travel_info", "trip_question",
            } or any(
                word in message
                for word in ("景点", "散步", "游览", "门票", "预约", "开放", "哪里", "介绍")
            ) else None,
        )
    planning = response.status in {"needs_clarification", "complete"}
    unconfirmed = "这部分信息目前还无法确认，暂时不能给出可靠结论。"
    if any(item.status == "unconfigured" for item in knowledge.map_lookups):
        unconfirmed += "地图查询尚未启用，具体位置暂未核实。"
    if knowledge.status == "answered":
        # 引用仅留在knowledge中供后台校验与追溯，聊天文字不展示编号。
        reply = "\n\n".join(point.text for point in knowledge.points)
        if "地图" not in reply and any(
            item.status == "unconfigured" for item in knowledge.map_lookups
        ):
            reply += "\n\n地图查询尚未启用，具体位置暂未核实。"
        if planning:
            reply += "\n\n" + response.reply
    elif knowledge.clarification:
        reply = knowledge.clarification
        if planning:
            reply += "\n\n" + response.reply
    elif planning:
        # 收集个人条件不依赖资料证明，但不能因此吞掉同一句中的知识问题。
        reply = response.reply + "\n\n" + unconfirmed
    else:
        reply = unconfirmed
    if knowledge.status == "answered" and knowledge.clarification:
        reply += "\n\n" + knowledge.clarification
    # 住宿证据不足时补问真正影响筛选的条件，不把模拟房价包装成可预订价格。
    if knowledge.status == "insufficient" and lodging_question and missing_stay:
        reply += "\n\n筛选住宿还需要" + "、".join(missing_stay) + "，请先补充这些条件。"
    if knowledge.web_search.status in {"unconfigured", "error", "empty"}:
        notice = {"unconfigured": "联网补查尚未启用，门票等时效信息可能不完整。",
                  "error": "本次联网补查失败，门票等时效信息暂未核实。",
                  "empty": "本次未找到可用的官方网页补充，门票等时效信息暂未核实。"}
        reply += "\n\n" + notice[knowledge.web_search.status]
    return response.model_copy(update={
        "reply": reply, "knowledge": knowledge,
        "status": response.status if planning else "knowledge",
    })
