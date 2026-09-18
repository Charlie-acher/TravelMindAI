"""测试层：验证口语化距离偏好先追问，以及补充字段失败不丢失可用推荐。"""

import json
from unittest.mock import Mock

import pytest

from app.schemas.document.answer import MapLookup, WebEvidence, WebSearchResult
from app.schemas.document.search import SearchResult
from app.services.chat.rag import ground_chat_response
from app.services.document.answer import answer_from_sources
from app.services.document.search import search_context
from tests.helpers import evidence
from tests.test_chat_rag import response

"""距离回答测试函数：口语距离偏好交给自然回答，不强制固定补问格式。"""

@pytest.mark.parametrize("message", [
    "杭州有哪些值得玩的景点？最好是三五个离得不远",
    "杭州几个景点挨着点，别离太远", "杭州景点近一点，步行游览",
])
def test_vague_proximity_asks_a_question(message: str) -> None:
    search, model = Mock(), Mock()
    search.search.return_value = SearchResult(items=[evidence()])
    model.generate_json.return_value = "{}"
    model.generate_text.return_value = "可以先按片区集中推荐，再根据实际交通调整。"
    result = ground_chat_response(response(message), search, model)
    assert result.reply == "可以先按片区集中推荐，再根据实际交通调整。"
    assert result.knowledge is not None
    assert result.knowledge.status == "insufficient" and not result.knowledge.attractions
    assert (result.knowledge.sources[0].hit.chunk.text
            == search.search.return_value.items[0].chunk.text)
    model.generate_text.assert_called_once()


"""偏好接续测试函数：简短回答带回上一轮地点，完整的新城市问题不借用旧话题。"""

def test_short_preference_reply_keeps_topic() -> None:
    context = "上一轮问题：杭州有哪些景点离得近？\n助手追问：步行多久以内？"
    assert "杭州" in search_context("步行半小时以内", context)
    assert search_context("上海有哪些景点？", context) == ""
    assert "杭州" in search_context("最多走个二三十分钟吧，老人多", context)


"""模型追问测试函数：纯问题可以没有事实引用，并随问答结果保存。"""

def test_model_can_ask_without_fact_citations() -> None:
    model = Mock()
    model.generate_json.return_value = json.dumps({
        "status": "insufficient", "points": [], "clarification": "你更喜欢看自然风景还是逛街？",
    })
    result = answer_from_sources("推荐些好玩的", [evidence()], model)
    assert result.clarification == "你更喜欢看自然风景还是逛街？"
    assert result.sources == []


"""可选详情测试函数：真实引文漏挂引用时补齐；错误门票单独回退，不能让介绍报错。"""

@pytest.mark.parametrize("valid", [True, False])
def test_ticket_detail_does_not_break_card(valid: bool) -> None:
    hit, model, maps, web = evidence(), Mock(), Mock(), Mock()
    quote = "杭州湖滨路步行街门票20元。"
    card = {"city": "杭州", "name": "湖滨路步行街", "description": "适合步行游览。",
            "reason": "散步。", "source_ids": [1],
            "ticket": {"status": "paid", "amount": "20" if valid else "90",
                       "summary": "门票20元。" if valid else "门票90元。",
                       "quote": quote, "source_id": 6}}
    model.generate_json.return_value = json.dumps({
        "status": "answered", "points": [{"text": "适合散步。", "source_ids": [1]}],
        "attractions": [card],
    })
    maps.lookup.return_value = MapLookup(city="杭州", name=card["name"], status="found",
                                         address="浙江省杭州市上城区湖滨路")
    web.search.return_value = WebSearchResult(status="found", items=[WebEvidence(
        id=6, title="杭州步行街", url="https://example.org/walk", content=quote,
    )])
    result = answer_from_sources("介绍湖滨路步行街", [hit], model, maps=maps, web=web)
    assert result.attractions[0].description == card["description"]
    assert result.attractions[0].ticket.status == ("paid" if valid else "unknown")
    if valid:
        assert 6 in result.attractions[0].source_ids
    else:
        assert result.clarification
        assert "90元" not in str(result.attractions) + str(result.points)


"""缺失票据测试函数：补充字段漏原句时只清空该字段，不能把90元留在正文。"""

def test_malformed_ticket_isolated_from_recommendation() -> None:
    model = Mock()
    model.generate_json.return_value = json.dumps({
        "status": "answered", "points": [{"text": "门票90元。", "source_ids": [1]}],
        "attractions": [{"city": "杭州", "name": "湖滨路步行街", "source_ids": [1],
                         "description": "步行游览门票90元。", "reason": "可散步。",
                         "ticket": {"status": "paid", "summary": "门票90元。"}}],
    })
    result = answer_from_sources("介绍步行街", [evidence()], model)
    assert result.attractions[0].ticket.status == "unknown"
    assert "90元" not in str(result.attractions) + str(result.points)
    assert result.clarification


"""介绍保留测试函数：坏票据只移除收费句，普通推荐不强迫用户追问地址门票。"""


def test_bad_ticket_keeps_description_without_forcing_map_followup() -> None:
    model = Mock()
    model.generate_json.return_value = json.dumps({
        "status": "answered", "points": [{"text": "可散步。", "source_ids": [1]}],
        "attractions": [{"city": "杭州", "name": "湖滨路步行街", "source_ids": [1],
                         "description": "湖边步行街，适合散步。门票90元。", "reason": "可散步。",
                         "ticket": {"status": "paid", "summary": "门票90元。"}}],
    })
    result = answer_from_sources("介绍步行街", [evidence()], model, resolve_locations=False)
    assert result.attractions[0].description == "湖边步行街，适合散步。"
    assert "90元" not in str(result)
    assert result.clarification is None


"""地址清理测试函数：地址引文无效时，正文重复的地址也不能显示。"""

@pytest.mark.parametrize("street", ["虚构路", "虚构大道"])
@pytest.mark.parametrize("malformed", [False, True])
def test_invalid_address_cannot_leak_through_description(street: str, malformed: bool) -> None:
    model = Mock()
    address = {"text": f"杭州市{street}999号", "source_id": 1}
    if not malformed:
        address["quote"] = f"地址是杭州市{street}999号"
    model.generate_json.return_value = json.dumps({
        "status": "answered", "points": [{"text": "可散步。", "source_ids": [1]}],
        "attractions": [{"city": "杭州", "name": "湖滨路步行街", "source_ids": [1],
                         "description": f"可以去杭州市{street}999号散步。", "reason": "可散步。",
                         "address_evidence": address}],
    })
    result = answer_from_sources("介绍步行街", [evidence()], model)
    assert result.attractions[0].address_evidence is None
    assert street not in str(result.attractions) + str(result.points)


"""地点依据补齐测试函数：分页正文缺城市时，可补挂本轮另一条同时说明城市和地点的证据。"""

def test_place_anchor_uses_existing_evidence_only() -> None:
    from app.schemas.document.answer import AttractionDraft
    from app.services.document.places import prepare_attractions

    item = AttractionDraft(city="杭州", name="西湖", description="沿湖散步。",
                           reason="可散步。", source_ids=[1])
    prepare_attractions([item], {1: "西湖可以沿湖散步。", 2: "杭州西湖游览介绍。"}, set())
    assert item.source_ids == [1, 2]
