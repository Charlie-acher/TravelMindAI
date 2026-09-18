"""聊天业务层：按本轮查询城市检索资料，资料不足时转为自然回答。

主聊天接口调用本文件，检索服务负责归属检查，问答服务负责模型与引用校验。
"""

import re
from itertools import zip_longest
from typing import Literal

from fastapi import HTTPException

from app.llm.budget import ModelInputLimitError
from app.llm.client import ModelOutputError
from app.llm.embeddings import EmbeddingError
from app.schemas.document.base import DocumentMetadata
from app.schemas.document.search import SearchHit
from app.schemas.requirement.chat import RequirementChatResponse
from app.services.baidu import BaiduMaps
from app.services.chat.conversation import natural_chat_response
from app.services.chat.events import progress
from app.services.document.answer import answer_from_sources
from app.services.document.search import DocumentSearchService
from app.services.document.vector_store import MilvusError
from app.services.requirement.extract import ModelClient
from app.services.web_search import WebSearchClient

"""特色问答函数：城市知识与周边筛选分别说明，只从检索证据生成菜品和店址。"""


def ground_food_response(
    response: RequirementChatResponse, search: DocumentSearchService,
    model: ModelClient, city: str, web: WebSearchClient | None = None,
    *, history_messages: list[dict[str, str]] | None = None,
) -> RequirementChatResponse:
    query = f"{city}特色菜、当地美食、餐馆名称及地点：{response.result.original_message}"
    hits = search.search(query[:800], 10, metadata=DocumentMetadata(
        city=city.removesuffix("市"), category="餐馆",
    )).items
    knowledge = answer_from_sources(
        query, hits, model, text_only=True,
        conversation_context=f"本次介绍{city}全城特色。周边消费和距离只约束单独的地图结果，"
                             "不能推断这些城市餐馆也满足那些条件。",
        history_messages=history_messages, query_cities=[city], standalone_query=True,
    )
    if knowledge.status != "answered":
        return natural_chat_response(response, model, city, hits, knowledge,
                                     history_messages=history_messages)
    text = ("\n\n".join(point.text for point in knowledge.points)
            if knowledge.status == "answered" else
            f"{city}特色菜和餐馆的可靠介绍这次还没查到，先不随意推荐店名或地址。")
    if response.dining:
        text = (response.reply + f"\n\n如果你也想了解整个{city}的特色，可以先看看这些；"
                "下面的城市介绍不代表都符合上面的距离和人均条件。\n\n" + text)
    return response.model_copy(update={"reply": text, "knowledge": knowledge})

"""知识提问判断函数：识别需查外部事实的问题，供补问分流和知识回答共用。"""


def is_knowledge_question(message: str) -> bool:
    return bool(re.search(
        r"哪些|哪里|介绍|推荐|景点|散步|门票|开放|预约|酒店|住宿|多少钱|有什么|怎么样|怎么去",
        message,
    ))


"""聊天依据生成函数：按当前问题取得参考资料，保留事实卡片检查并允许自然回答。"""


def ground_chat_response(
    response: RequirementChatResponse,
    search: DocumentSearchService,
    model: ModelClient,
    destination: str | None = None,
    history_context: str | None = None,
    maps: BaiduMaps | None = None,
    web: WebSearchClient | None = None,
    *, retrieval_query: str | None = None, query_cities: list[str] | None = None,
    history_messages: list[dict[str, str]] | None = None,
    reference_only: bool = False,
    retrieval_category: Literal["景点", "餐馆", "住宿"] | None = None,
) -> RequirementChatResponse:
    message = response.result.original_message
    cities = list(dict.fromkeys(query_cities if query_cities is not None else
                                ([destination] if destination else [])))
    query = retrieval_query if retrieval_query is not None else message
    context = history_context or ""
    if cities:
        context = "本轮查询城市：" + "、".join(cities) + "\n" + context
    hits: dict[str, SearchHit] = {}
    # 单城严格按元数据过滤；比较问题逐城检索，不让某一城挤掉其余城市的证据。
    by_city: list[list[SearchHit]] = []
    try:
        search_cities: list[str | None] = list(cities) if cities else [None]
        for city in search_cities:
            prefix = f"{city}：" if city else ""
            found: dict[str, SearchHit] = {}
            for offset in range(0, len(query), 800 - len(prefix)):
                items = search.search(prefix + query[offset:offset + 800 - len(prefix)], 20,
                                      metadata=DocumentMetadata(
                                          city=city.removesuffix("市") if city else None,
                                          category=retrieval_category,
                                      ), include_general=True).items
                for hit in items:
                    text = hit.chunk.text.strip()
                    if "\n" not in text and text.lstrip("# ") in hit.chunk.section_path:
                        continue
                    # 文档首页只有来源网址的片段不能解释景点，不占回答证据名额。
                    if re.fullmatch(r"(?:来源[：:]\s*)?https?://\S+", text):
                        continue
                    key = str(hit.chunk.id)
                    if key not in found or hit.score > found[key].score:
                        found[key] = hit
            # 只在本轮召回候选中按文件交替取材，避免同一攻略占满所有证据名额。
            documents: dict[str, list[SearchHit]] = {}
            for hit in sorted(found.values(), key=lambda item: item.score, reverse=True):
                documents.setdefault(str(hit.chunk.document_id), []).append(hit)
            by_city.append([hit for rank in zip_longest(*documents.values())
                            for hit in rank if hit is not None])
    except (EmbeddingError, MilvusError):
        progress("retrieval", "知识库暂时不可用，本轮将先用模型知识回答")
        return natural_chat_response(response, model, context, history_messages=history_messages)
    for rank in zip_longest(*by_city):
        for hit in rank:
            if hit is not None:
                hits.setdefault(str(hit.chunk.id), hit)
    evidence = list(hits.values())[:5]
    progress("retrieval", f"知识库检索完成，选取{len(evidence)}段资料作为回答参考"
             if evidence else "知识库没有找到匹配资料，将说明可用信息与待确认内容")
    # 普通开场和个人条件无需填景点卡；其他知识问题仍可使用有事实核对的结构化回答。
    if reference_only or (retrieval_query is None and not is_knowledge_question(message)):
        return natural_chat_response(response, model, context, evidence,
                                     history_messages=history_messages)
    try:
        knowledge = answer_from_sources(
            query, evidence, model, conversation_context=context, maps=maps,
            query_cities=cities, history_messages=history_messages,
            original_question=message, standalone_query=True,
            resolve_locations=False,
        )
    except (ModelOutputError, ModelInputLimitError):
        return natural_chat_response(response, model, context, evidence,
                                     history_messages=history_messages)
    except HTTPException as error:
        if error.status_code != 502:
            raise
        return natural_chat_response(response, model, context, evidence,
                                     history_messages=history_messages)
    if knowledge.status != "answered":
        return natural_chat_response(response, model, context, evidence, knowledge,
                                     history_messages=history_messages)
    reply = "\n\n".join(point.text for point in knowledge.points)
    if knowledge.clarification and not knowledge.attractions:
        reply += "\n\n" + knowledge.clarification
    return response.model_copy(update={"reply": reply, "knowledge": knowledge,
        "status": response.status if response.status in {
            "needs_clarification", "complete"} else "knowledge"})
