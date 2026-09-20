"""行程工具层：执行白名单检索与地图查询，保存真实返回的来源和地点。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from langchain_core.tools import ToolException
from pydantic import Field, StringConstraints

from app.schemas.document.base import DocumentChunk, DocumentMetadata
from app.schemas.itinerary import PlanPlace, PlanSource, TravelPlan
from app.services.baidu import BaiduMaps
from app.services.chat.events import progress
from app.services.document.search import DocumentSearchService
from app.services.web_search import WebSearchClient


class PlanTools:
    """行程工具箱类：每轮请求独立保存证据和调用次数，没有全局共享状态。"""

    """初始化函数：复用现有外部连接；旧版本证据可供局部修改，不额外重复查询。"""

    def __init__(self, destination: str, search: DocumentSearchService, maps: BaiduMaps,
                 web: WebSearchClient | None, old: TravelPlan | None) -> None:
        self.destination, self.search, self.maps, self.web = destination, search, maps, web
        self.sources: dict[str, PlanSource] = {}
        self.places: dict[str, PlanPlace] = {}
        self.knowledge_chunks: dict[str, UUID] = {}  # 只准展开本轮检索实际返回的共享来源。
        self.calls = 0
        if old is not None and old.destination == destination:
            for day in old.days:
                for activity in day.activities:
                    self.places[activity.place.id] = activity.place
                    for source in activity.place.sources:
                        self.sources[source.id] = source

    """编号分配函数：跨多轮检索和旧草稿使用不重复的短编号。"""

    def _source(self, kind: Literal["knowledge", "web"], title: str, text: str,
                url: str | None = None, *, chunk: DocumentChunk | None = None) -> PlanSource:
        prefix = "k" if kind == "knowledge" else "w"
        index = 1
        while f"{prefix}{index}" in self.sources:
            index += 1
        source = PlanSource.model_validate(dict(
            id=f"{prefix}{index}", kind=kind, title=title, text=text, url=url,
            document_id=chunk.document_id if chunk else None,
            chunk_id=chunk.id if chunk else None,
            page_number=chunk.page_number if chunk else None,
            section_path=chunk.section_path if chunk else []))
        self.sources[source.id] = source
        if chunk is not None:
            self.knowledge_chunks[source.id] = chunk.id
        return source

    """调用计数函数：检索、原文展开和地图共用12次额度，提交与修正不消耗查询额度。"""

    def _reserve(self) -> None:
        if self.calls >= 12:
            raise ToolException("本轮工具调用已达上限，请据现有证据规划或说明缺项")
        self.calls += 1

    """知识检索函数：只查当前目的地，保存实际命中的原文和文件名。"""

    def knowledge_search(self, query: Annotated[str, StringConstraints(
            strip_whitespace=True, min_length=2, max_length=300)]) -> dict[str, object]:
        self._reserve()
        progress("plan_search", "正在检索与行程有关的旅行资料")
        hits = self.search.search(f"{self.destination} {query}", limit=5,
            metadata=DocumentMetadata(city=self.destination.removesuffix("市")),
            include_general=True)
        return {"sources": [self._source(
            "knowledge", hit.file_name, hit.chunk.text, chunk=hit.chunk,
        ).model_dump(mode="json") for hit in hits.items]}

    """原文展开函数：只接受本轮来源编号，回查同城同节片段并保留出处。"""

    def knowledge_read(self, source_id: Annotated[str, Field(
            min_length=1, max_length=20)]) -> dict[str, object]:
        self._reserve()
        if source_id not in self.knowledge_chunks:
            raise ToolException("只能展开本轮知识检索返回的来源编号")
        progress("plan_read", "正在读取命中章节附近的原文，补充行程依据")
        try:
            context = self.search.read_context(self.knowledge_chunks[source_id],
                metadata=DocumentMetadata(city=self.destination.removesuffix("市")))
        except HTTPException as error:
            if error.status_code != 404:
                raise
            raise ToolException("这段原文已不可用，请重新检索其他资料") from None
        sources = []
        for chunk in context.items:
            # 展开包含原命中段时沿用其编号；新检索仍按原有规则分配来源编号。
            source = next((self.sources[key] for key, identifier in self.knowledge_chunks.items()
                           if identifier == chunk.id), None)
            source = source or self._source("knowledge", context.file_name, chunk.text, chunk=chunk)
            sources.append(source.model_dump(mode="json"))
        return {"sources": sources, "truncated": context.truncated}

    """网页检索函数：知识不足时补查公开原文，不把未配置或空结果当作证据。"""

    def web_search(self, query: Annotated[str, StringConstraints(
            strip_whitespace=True, min_length=2, max_length=300)]) -> dict[str, object]:
        self._reserve()
        progress("plan_web", "正在补查公开旅行资料")
        if self.web is None:
            return {"status": "unconfigured", "sources": []}
        found = self.web.search(f"{self.destination} {query}")
        return {"status": found.status, "sources": [self._source(
            "web", hit.title, hit.content, str(hit.url)).model_dump(mode="json")
            for hit in found.items]}

    """地图核对函数：只查询真实来源中的地点，复用已核对结果并分配地点编号。"""

    def map_lookup(self, name: Annotated[str, StringConstraints(
            strip_whitespace=True, min_length=2, max_length=80)],
            source_ids: Annotated[list[str], Field(min_length=1, max_length=5)],
    ) -> dict[str, object]:
        self._reserve()
        sources = [self.sources[key] for key in source_ids if key in self.sources]
        if (len(sources) != len(source_ids)
                or not any(name in source.text for source in sources)
                or any(source.kind == "attachment" and source.text.splitlines()[0] != name
                       for source in sources)):
            return {"error": "只能查询原文中出现的地点，请先检索并引用真实来源编号"}
        for place in self.places.values():
            if place.map.name == name:
                # 复用坐标时仍记录这轮采用的附件依据，不改旧草稿里的对象。
                combined = {source.id: source for source in [*sources, *place.sources]}
                place = place.model_copy(update={"sources": list(combined.values())[:5]})
                self.places[place.id] = place
                return {"place": place.model_dump(mode="json")}
        progress("plan_map", f"正在核对{name}的位置")
        result = self.maps.lookup(self.destination, name)
        if result.status != "found" or result.match_kind != "poi" or result.location is None:
            return {"status": result.status, "error": "未核对到具体景点，请换用其他有依据的地点"}
        index = 1
        while f"p{index}" in self.places:
            index += 1
        place = PlanPlace(id=f"p{index}", map=result, sources=sources)
        self.places[place.id] = place
        return {"place": place.model_dump(mode="json")}
