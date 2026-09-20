"""测试层：用隔离数据库核对混合召回、原文展开与行程工具的证据边界。"""

import json
from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from langchain_core.tools import ToolException
from sqlalchemy import update

from app.models.document import DocumentRecord
from app.schemas.document.answer import GeoPoint, MapLookup
from app.schemas.document.base import DocumentMetadata
from app.schemas.requirement.base import TravelRequestExtraction
from app.services.document.search import DocumentSearchService
from app.services.document.service import DocumentService
from app.services.itinerary.graph import plan_trip
from app.services.itinerary.tools import PlanTools
from tests.helpers import RecordingModel, answer

"""资料准备函数：走真实上传和分段流程，返回可核对的原文片段。"""


def guide(engine, directory, text, *, city="长沙", owner="owner-a", category="景点"):
    documents = DocumentService(engine, directory, owner)
    saved = documents.upload(BytesIO(text.encode()), "攻略.md", "text/markdown")
    documents.update_metadata(saved.document.id, DocumentMetadata(city=city, category=category))
    return documents.generate_chunks(saved.document.id).items


"""检索替身函数：只替换外部向量和收费模型，正文与权限查询使用真实SQL。"""


def search_service(engine, candidates=(), *, exists=True):
    vectors = Mock()
    vectors.exists.return_value = exists
    vectors.search.return_value = [(chunk.id, score) for chunk, score in candidates]
    model = Mock()
    model.embed.return_value.vectors = [[1., 0.]]
    return DocumentSearchService(engine, vectors, model, "owner-a")


"""点名召回测试函数：语义通道漏掉目标时，中文标题和正文关键词仍可找回它。"""


@pytest.mark.parametrize("text,query", [
    ("## 岳麓书院\n\n院内有历史建筑。", "长沙岳麓书院怎么安排？"),
    ("名称（name）：岳麓书院\n位于岳麓山下。", "长沙岳麓书院怎么安排？"),
    ("## 坡子街\n\n这里有臭豆腐摊位。", "长沙 臭豆腐"),
])
def test_lexical_recovers_missed_place(store_engine, tmp_path, text, query):
    target = guide(store_engine, tmp_path, text)
    noise = guide(store_engine, tmp_path, "## 岳麓山\n\n长沙的山林风景。")
    service = search_service(store_engine, [(noise[-1], .99)])
    hits = service.search(query, 5, metadata=DocumentMetadata(city="长沙")).items
    assert hits[0].chunk.id == target[-1].id
    assert len({hit.chunk.id for hit in hits}) == len(hits)
    assert noise[-1].id in {hit.chunk.id for hit in hits}


"""范围测试函数：关键词召回同样遵守归属、城市、分类、资料编号与停用状态。"""


def test_lexical_scope_and_unindexed_text(store_engine, tmp_path):
    target = guide(store_engine, tmp_path, "## 西湖\n\n杭州西湖可散步。", city="杭州")
    guide(store_engine, tmp_path, "## 西湖\n\n异地西湖。", city="福州")
    guide(store_engine, tmp_path, "## 西湖\n\n私人西湖资料。", city="杭州", owner="owner-b")
    guide(store_engine, tmp_path, "## 西湖\n\n西湖边餐馆。", city="杭州", category="餐馆")
    retired = guide(store_engine, tmp_path, "## 西湖\n\n已停用资料。", city="杭州")
    with store_engine.begin() as connection:
        connection.execute(update(DocumentRecord).where(
            DocumentRecord.id == retired[0].document_id,
        ).values(status="deleting", error_message="待清理"))
    service = search_service(store_engine, exists=False)
    hits = service.search("西湖散步", 5, metadata=DocumentMetadata(
        city="杭州", category="景点")).items
    assert [hit.chunk.id for hit in hits] == [target[-1].id]
    assert service.search("西湖", 5, target[0].document_id).items[0].chunk.id == target[-1].id
    assert service.search("无匹配内容", 5, target[0].document_id).items == []


"""语义保留测试函数：没有相同文字的偏好问题仍采用语义候选及其排序。"""


def test_semantic_preference_survives(store_engine, tmp_path):
    target = guide(store_engine, tmp_path, "## 湖滨\n\n道路平坦，沿途设有座椅。")
    other = guide(store_engine, tmp_path, "## 山顶\n\n需要攀登长石阶。")
    service = search_service(store_engine, [(target[-1], .9), (other[-1], .5)])
    hits = service.search("带老人轻松游玩", 2).items
    assert [hit.chunk.id for hit in hits] == [target[-1].id, other[-1].id]


"""名称具体度测试函数：同时命中父地点和子项目时，优先用户点名的完整项目。"""


def test_longer_exact_name_beats_parent_place(store_engine, tmp_path):
    parent = guide(store_engine, tmp_path, "## 西湖\n\n湖边有游船服务。", city="杭州")
    target = guide(store_engine, tmp_path, "## 西湖游船\n\n从指定码头登船。", city="杭州")
    service = search_service(store_engine, [(parent[-1], .99)])
    assert service.search("杭州西湖游船", 1, metadata=DocumentMetadata(
        city="杭州")).items[0].chunk.id == target[-1].id


"""真实标题格式测试函数：来源记录键不算景点名称，城市前缀也不能从专名中删掉。"""


def test_record_key_heading_and_city_in_place_name(store_engine, tmp_path):
    target = guide(store_engine, tmp_path,
                   "## 苏州博物馆｜attractions/suzhou/7\n\n建筑和展品介绍。", city="苏州")
    other = guide(store_engine, tmp_path, "## 苏绣博物馆\n\n馆藏介绍。", city="苏州")
    service = search_service(store_engine, [(other[-1], .99)])
    assert service.search("苏州博物馆", 1, metadata=DocumentMetadata(
        city="苏州")).items[0].chunk.id == target[-1].id


"""短名称测试函数：普通中文问句也能找回没有标题的两字地点。"""


@pytest.mark.parametrize("query", ["西湖怎么安排？", "杭州西湖门票多少钱？"])
def test_short_name_in_plain_text(store_engine, tmp_path, query):
    target = guide(store_engine, tmp_path, "西湖位于杭州西部。", city="杭州")
    service = search_service(store_engine, exists=False)
    assert service.search(query, 5, metadata=DocumentMetadata(
        city="杭州")).items[0].chunk.id == target[0].id


"""通道故障测试函数：有真实文字证据时仍返回，双通道无依据不能伪造成功。"""


def test_vector_failure_retains_lexical_evidence(store_engine, tmp_path):
    from app.llm.embeddings import EmbeddingError

    target = guide(store_engine, tmp_path, "## 天心阁\n\n有历史建筑。")
    service = search_service(store_engine)
    service._model.embed.side_effect = EmbeddingError("模型不可用")
    assert service.search("天心阁", 5).items[0].chunk.id == target[-1].id
    with pytest.raises(EmbeddingError):
        service.search("无匹配问题", 5)


"""文字转义测试函数：关键词中的下划线是原文字面值，不能成为SQL通配符。"""


def test_lexical_query_escapes_wildcards(store_engine, tmp_path):
    target = guide(store_engine, tmp_path, "route_a的入口在南门。")
    guide(store_engine, tmp_path, "routexa的入口在北门。")
    service = search_service(store_engine, exists=False)
    assert [hit.chunk.id for hit in service.search("route_a", 5).items] == [target[0].id]


"""章节展开测试函数：只读连续同节，保留片段位置，重复标题不跨越其他章节。"""


def test_read_context_preserves_section_and_scope(store_engine, tmp_path):
    chunks = guide(store_engine, tmp_path,
        "## 岳麓书院\n\n院内历史建筑。\n\n入口有石阶。\n\n"
        "## 橘子洲\n\n洲上可散步。\n\n## 岳麓书院\n\n另一个章节。")
    anchor = next(chunk for chunk in chunks if chunk.text == "院内历史建筑。")
    service = search_service(store_engine)
    context = service.read_context(anchor.id, metadata=DocumentMetadata(city="长沙"))
    assert [chunk.text for chunk in context.items] == [
        "## 岳麓书院", "院内历史建筑。", "入口有石阶。"]
    assert context.items[1] == anchor
    assert not context.truncated
    for identifier, city in [(uuid4(), "长沙"), (anchor.id, "杭州")]:
        with pytest.raises(HTTPException) as denied:
            service.read_context(identifier, metadata=DocumentMetadata(city=city))
        assert denied.value.status_code == 404
    other = DocumentSearchService(store_engine, Mock(), Mock(), "owner-b")
    with pytest.raises(HTTPException) as denied:
        other.read_context(anchor.id)
    assert denied.value.status_code == 404
    with store_engine.begin() as connection:
        connection.execute(update(DocumentRecord).where(
            DocumentRecord.id == anchor.document_id,
        ).values(status="deleting", error_message="待清理"))
    with pytest.raises(HTTPException) as stopped:
        service.read_context(anchor.id)
    assert stopped.value.status_code == 404


"""展开上限测试函数：长章节以命中点为中心，最多五段；无标题时只读同一原文单元。"""


def test_read_context_limits_and_headingless_units(store_engine, tmp_path):
    chunks = guide(store_engine, tmp_path,
                   "## 岳麓书院\n\n" + "\n\n".join(f"正文第{i}段。" for i in range(12)))
    anchor = chunks[7]
    service = search_service(store_engine)
    context = service.read_context(anchor.id)
    assert len(context.items) == 5 and context.truncated
    assert anchor in context.items
    assert sum(len(chunk.text) for chunk in context.items) <= 4000
    plain = guide(store_engine, tmp_path, "同一页长正文。" * 300 + "\n\n另一独立段落。")
    context = service.read_context(plain[1].id)
    assert all(chunk.section_order == plain[1].section_order for chunk in context.items)
    assert all("另一独立段落" not in chunk.text for chunk in context.items)


"""重复标题测试函数：同名标题重新出现也代表新章节，不能跨过标题继续展开。"""


def test_read_context_stops_at_repeated_heading(store_engine, tmp_path):
    chunks = guide(store_engine, tmp_path,
                   "## 老街\n\n旧章节。\n\n## 老街\n\n新章节。")
    service = search_service(store_engine)
    assert [chunk.text for chunk in service.read_context(chunks[1].id).items] == [
        "## 老街", "旧章节。"]
    assert [chunk.text for chunk in service.read_context(chunks[3].id).items] == [
        "## 老街", "新章节。"]


"""工具证据测试函数：只展开本轮已搜到的编号，展开正文可用于地图核对并保存出处。"""


def test_plan_read_tool_uses_only_retrieved_sources(store_engine, tmp_path):
    chunks = guide(store_engine, tmp_path, "## 老街\n\n步行路线介绍。\n\n可顺路到天心阁。")
    service = search_service(store_engine, [(chunks[1], .9)])
    tools = PlanTools("长沙", service, Mock(), None, None)
    result = tools.knowledge_search("步行路线")
    source = next(item for item in result["sources"] if "步行路线" in item["text"])
    with pytest.raises(ToolException, match="本轮"):
        tools.knowledge_read("k999")
    expanded = tools.knowledge_read(source["id"])
    found = next(item for item in expanded["sources"] if "天心阁" in item["text"])
    assert found["document_id"] == str(chunks[0].document_id)
    assert found["chunk_id"] == str(chunks[2].id)
    assert found["section_path"] == ["老街"]
    assert tools.sources[found["id"]].text == chunks[2].text
    assert tools.calls <= 3
    tools.calls = 12
    with pytest.raises(ToolException, match="上限"):
        tools.knowledge_read(source["id"])


"""框架闭环测试函数：原生工具消息串联检索、展开、地图与提交，来源位置随草稿保存。"""


def test_agent_reads_context_before_planning(store_engine, tmp_path):
    chunks = guide(store_engine, tmp_path, "## 老街\n\n步行路线介绍。\n\n可顺路到天心阁。")
    search = search_service(store_engine, [(chunks[1], .9)])
    maps = Mock()
    maps.lookup.side_effect = lambda city, name: MapLookup(
        city=city, name=name, status="found", match_kind="poi", poi_id=name,
        location=GeoPoint(longitude=112.9, latitude=28.2))
    model = RecordingModel([json.dumps(value, ensure_ascii=False) for value in [
        {"tools": [{"tool": "knowledge_search", "query": "步行路线"}]},
        {"tools": [{"tool": "knowledge_read", "source_id": "k1"}]},
        {"tools": [{"tool": "map_lookup", "name": "老街", "source_ids": ["k2"]},
                   {"tool": "map_lookup", "name": "天心阁", "source_ids": ["k3"]}]},
        {"days": [{"day": day, "activities": [{"place_id": f"p{day}",
                   "start_time": "09:00", "duration_minutes": 90,
                   "transport": "walk", "transfer_minutes": 0}]} for day in (1, 2)]},
    ]])
    native = model.model
    requirements = TravelRequestExtraction.model_validate(answer(
        destination="长沙", days=2, travelers=1, total_budget="3000"))
    result = plan_trip("按步行路线安排两天", requirements, None, native, search, maps, None)
    assert result.plan is not None
    assert "knowledge_read" not in native.bindings[0][0]
    assert "knowledge_read" in native.bindings[1][0]
    source = result.plan.days[1].activities[0].place.sources[0]
    assert source.chunk_id == chunks[2].id
    assert source.document_id == chunks[2].document_id
    assert source.section_path == ["老街"] and source.text == chunks[2].text
