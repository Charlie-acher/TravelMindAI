"""测试层：验证逐地点补查门票和详细地址，拒绝无依据的地址。"""

import json
from unittest.mock import Mock

import pytest

from app.schemas.document.answer import MapLookup, WebEvidence, WebSearchResult
from app.services.document.answer import answer_from_sources
from tests.helpers import evidence

"""定向补查测试函数：宽泛搜索不足时，第二遍回答可以使用新增编号的门票和地址原文。"""

def test_missing_details_trigger_targeted_search() -> None:
    hit, model, web, maps = evidence(), Mock(), Mock(), Mock()
    hit.chunk.text = "杭州湖滨路步行街适合步行，门票及地址待核实。"
    draft = {"city": "杭州", "name": "湖滨路步行街", "description": "适合步行。",
             "reason": "可以散步。", "source_ids": [1]}
    first = {"status": "answered", "points": [{"text": "可以散步。", "source_ids": [1]}],
             "attractions": [draft]}
    quote = "杭州湖滨路步行街步行游览免费，地址：杭州市上城区湖滨路。"
    final = {**first, "attractions": [{**draft, "source_ids": [1, 7],
        "ticket": {"status": "free", "summary": "步行游览免费。", "source_id": 7,
                   "quote": quote},
        "address_evidence": {"text": "杭州市上城区湖滨路", "source_id": 7, "quote": quote},
    }]}
    model.generate_json.side_effect = [json.dumps(first), json.dumps(final)]
    maps.lookup.return_value = MapLookup(city="杭州", name=draft["name"], status="no_match")
    web.search.side_effect = [
        WebSearchResult(status="found", items=[WebEvidence(
            id=6, title="散步推荐", url="https://example.org/walk", content="杭州的步行路线。",
        )]),
        WebSearchResult(status="found", items=[WebEvidence(
            id=6, title="街区游览", url="https://example.org/walk", content=quote,
        )]),
    ]
    result = answer_from_sources("杭州哪里适合散步", [hit], model, web=web, maps=maps)
    assert web.search.call_count == 2
    query = web.search.call_args.args[0]
    assert all(word in query for word in ("杭州", "湖滨路步行街", "门票", "地址"))
    assert result.attractions[0].ticket.status == "free"
    assert result.attractions[0].address_evidence.text == "杭州市上城区湖滨路"
    assert [item.id for item in result.web_search.items] == [6, 7]
    assert result.attractions[0].location.status == "no_match"
    assert result.web_search.supplemental_queries[0].status == "found"


"""地址拒绝测试函数：地址必须来自同地点的完整原句，不能借用别的景点地址。"""

def test_address_requires_verbatim_same_place_evidence() -> None:
    from app.schemas.document.answer import AttractionDraft
    from app.services.document.places import validate_attractions

    data = {"city": "杭州", "name": "湖滨路步行街", "description": "步行。",
            "reason": "散步。", "source_ids": [1, 6],
            "address_evidence": {"text": "不存在的路99号", "source_id": 6,
                                 "quote": "杭州另一景点地址：不存在的路99号。"}}
    draft = AttractionDraft.model_validate(data)
    with pytest.raises(ValueError, match="地址"):
        validate_attractions(
            [draft], {1: "杭州湖滨路步行街", 6: data["address_evidence"]["quote"]}, set(),
        )


"""地图地址优先测试函数：已有工具地址时，不要求模型重复抄写或为它编造原文引用。"""

def test_verified_map_address_discards_redundant_model_address() -> None:
    from app.schemas.document.answer import AttractionDraft
    from app.services.document.places import validate_attractions

    draft = AttractionDraft.model_validate({
        "city": "杭州", "name": "湖滨路步行街", "description": "适合步行。",
        "reason": "散步。", "source_ids": [1],
        "address_evidence": {"text": "地图返回的凤起路地址", "source_id": 1,
                             "quote": "地图返回的凤起路地址"},
    })
    lookup = MapLookup(city="杭州", name="湖滨路步行街", status="found",
                       address="浙江省杭州市上城区凤起路539-4号")
    validate_attractions([draft], {1: "杭州湖滨路步行街适合步行。"}, set(), [lookup])
    assert draft.address_evidence is None


"""免票措辞测试函数：明确无需门票可以核对，不能只支持“免费”两个字。"""

def test_ticket_accepts_explicit_no_ticket_statement() -> None:
    from app.schemas.document.answer import AttractionDraft
    from app.services.document.places import validate_attractions

    quote = "杭州湖滨路步行街步行游览无需门票。"
    draft = AttractionDraft.model_validate({
        "city": "杭州", "name": "湖滨路步行街", "description": "步行游览。",
        "reason": "散步。", "source_ids": [6],
        "ticket": {"status": "free", "summary": "步行无需门票。", "source_id": 6, "quote": quote},
    })
    validate_attractions([draft], {6: quote}, set())


"""错地详情测试函数：同篇文章包含目标名称，也不能借用另一景点的地址和票价。"""

@pytest.mark.parametrize("field", ["address_evidence", "ticket"])
@pytest.mark.parametrize("prefix", ["", "杭州湖滨路步行街适合散步；"])
def test_mixed_article_cannot_lend_another_places_details(field: str, prefix: str) -> None:
    from app.schemas.document.answer import AttractionDraft
    from app.services.document.places import validate_attractions

    quote = prefix + "杭州另一景点门票100元，地址：上城区甲路99号。"
    source = "杭州湖滨路步行街步行推荐。" + quote
    data = {"city": "杭州", "name": "湖滨路步行街", "description": "步行。",
            "reason": "散步。", "source_ids": [6]}
    data[field] = ({"text": "上城区甲路99号", "source_id": 6, "quote": quote}
                   if field == "address_evidence" else
                   {"status": "paid", "summary": "门票100元", "amount": "100",
                    "source_id": 6, "quote": quote})
    with pytest.raises(ValueError):
        validate_attractions([AttractionDraft.model_validate(data)], {6: source}, set())


"""免费范围测试函数：特殊人群免费或同句有收费时，不能隐藏普通游客的门票信息。"""

@pytest.mark.parametrize("quote", [
    "杭州湖滨路步行街儿童免费，成人票20元。",
    "杭州湖滨路步行街儿童免费。",
    "杭州湖滨路步行街2026年9月15日免费。",
])
def test_limited_free_admission_requires_partial_status(quote: str) -> None:
    from app.schemas.document.answer import AttractionDraft
    from app.services.document.places import validate_attractions

    draft = AttractionDraft.model_validate({
        "city": "杭州", "name": "湖滨路步行街", "description": "步行。",
        "reason": "散步。", "source_ids": [6],
        "ticket": {"status": "free", "summary": "免费", "source_id": 6, "quote": quote},
    })
    with pytest.raises(ValueError):
        validate_attractions([draft], {6: quote}, set())
