"""外部工具连接层：检索允许网站的公开资料，为知识库回答补充网页证据。

使用现有httpx调用Tavily；不采用搜索服务生成的回答，也不自行打开模型给出的链接。
"""

from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.schemas.document.answer import WebEvidence, WebSearchResult


class WebSearchClient:
    """网页搜索客户端类：限制来源、数量和文本长度，查询失败不冒充没有相关信息。"""

    """初始化函数：借用连接并保存独立密钥；未配置时不联网。"""

    def __init__(self, settings: Settings, http: httpx.Client) -> None:
        self.key = settings.tavily_api_key
        self.domains = [item.lower().strip().strip(".") for item in settings.travel_web_domains
                        if item.strip()]
        self.http = http

    """搜索函数：一次最多五条网页，只保留原文摘要及其位置和时间。"""

    def search(self, query: str) -> WebSearchResult:
        if self.key is None or not self.key.get_secret_value().strip() or not self.domains:
            return WebSearchResult(status="unconfigured")
        try:
            response = self.http.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.key.get_secret_value()}"},
                json={"query": query[:500], "search_depth": "basic", "max_results": 5,
                      "include_domains": self.domains, "include_answer": False,
                      "include_raw_content": False, "include_published_date": True},
                timeout=8,
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("results"), list):
                return WebSearchResult(status="error")
            items: list[WebEvidence] = []
            seen: set[str] = set()
            for row in body["results"][:5]:
                if not isinstance(row, dict):
                    continue
                url, content = row.get("url"), row.get("content")
                if not isinstance(url, str) or not isinstance(content, str) or not content.strip():
                    continue
                parts = urlsplit(url)
                host = parts.hostname or ""
                if (parts.scheme not in {"http", "https"} or parts.username or parts.password
                        or not any(host == domain or host.endswith("." + domain)
                                   for domain in self.domains) or url in seen):
                    continue
                try:
                    items.append(WebEvidence(
                        id=6 + len(items), title=str(row.get("title", ""))[:200],
                        url=url, content=content.strip()[:2000],
                        published_date=row.get("published_date")
                        if isinstance(row.get("published_date"), str) else None,
                    ))
                    seen.add(url)
                except ValidationError:
                    continue
            return WebSearchResult(status="found" if items else "empty", items=items)
        except (httpx.HTTPError, ValueError):
            # 不把可能含密钥或服务商内部信息的异常原文传给前端。
            return WebSearchResult(status="error")
