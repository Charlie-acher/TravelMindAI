"""测试层：核对网页搜索范围、证据截取和缺配置行为，不调用收费服务。"""

import httpx

from app.config import Settings

"""搜索测试函数：只接收允许域名的正文摘要，忽略搜索服务生成的答案和非法链接。"""


def test_search_preserves_bounded_evidence() -> None:
    from app.services.web_search import WebSearchClient

    """HTTP替身函数：检查搜索参数，返回可用、越界与空内容三种网页。"""

    def handle(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content)
        assert body["include_answer"] is False and body["max_results"] == 5
        assert body["include_domains"] == ["gov.cn"]
        return httpx.Response(200, json={"answer": "不能使用的模型摘要", "results": [
            {"title": "公告", "url": "https://www.hangzhou.gov.cn/notice",
             "content": "官方公告正文" * 400, "published_date": "2025-01-01"},
            {"title": "假域名", "url": "https://gov.cn.evil.example/n", "content": "无效"},
            {"title": "空正文", "url": "https://www.hangzhou.gov.cn/empty", "content": ""},
        ]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = WebSearchClient(Settings(tavily_api_key="test"), http).search("杭州景点")
    assert result.status == "found" and len(result.items) == 1
    assert result.items[0].id == 6 and len(result.items[0].content) == 2000
    assert result.items[0].published_date == "2025-01-01"


"""失败测试函数：缺密钥不请求，超时只保存失败状态，不泄露异常中的密钥。"""


def test_search_missing_key_and_failure() -> None:
    from app.services.web_search import WebSearchClient

    """失败替身函数：只在有密钥时触发超时。"""

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("test-secret", request=request)
    with httpx.Client(transport=httpx.MockTransport(fail)) as http:
        missing = WebSearchClient(Settings(tavily_api_key=None), http).search("杭州")
        assert missing.status == "unconfigured"
        result = WebSearchClient(Settings(tavily_api_key="test-secret"), http).search("杭州")
    assert result.status == "error" and "test-secret" not in result.model_dump_json()
