"""价格测试层：官方公布价保留原文与日期，不把无依据金额或旧活动价当实时报价。"""

from datetime import date
from types import SimpleNamespace

import pytest

from app.schemas.document.answer import WebEvidence, WebSearchResult
from app.schemas.travel_price import PublishedPrice
from app.services.travel_prices import add_prices, lookup_price


class Search:
    """搜索替身类：提供官方片段，不访问外部网络。"""

    def search(self, query, **kwargs):
        return WebSearchResult(status="found", items=[WebEvidence(id=6,
            title="景区收费公示", url="https://www.hangzhou.gov.cn/ticket", content=
            "雷峰塔成人门票40元/人，儿童优惠以现场条件为准。", published_date="2026-09-20")])


class Model:
    """模型替身类：返回待核实的结构，不决定来源网址和查询时间。"""

    def generate_json(self, messages):
        return '{"source_id":6,"amount":"40","unit":"成人/人","conditions":"儿童优惠另核对",' \
               '"evidence":"雷峰塔成人门票40元/人","valid_from":null,"valid_until":null}'


"""公布价测试函数：只称官方公布价，不承诺具体出行日或库存。"""

def test_official_price_keeps_source_and_applicability():
    result = lookup_price("杭州", "雷峰塔", date(2026, 10, 1), Search(), Model())
    assert result.status == "published" and str(result.amount) == "40"
    assert result.travel_date == date(2026, 10, 1)
    assert result.source_url == "https://www.hangzhou.gov.cn/ticket"
    assert result.date_confirmed is False


"""无依据测试函数：模型虚构金额时只保留查询入口，不写入价格。"""

def test_invented_price_is_not_a_quote():
    class Wrong(Model):
        def generate_json(self, messages):
            return super().generate_json(messages).replace('"40"', '"30"')
    result = lookup_price("杭州", "雷峰塔", None, Search(), Wrong())
    assert result.status == "unavailable" and result.amount is None


"""取消收费测试函数：原文提到旧75元已取消，不能因为数字存在就展示旧收费。"""

def test_cancelled_price_is_not_current():
    class CancelledSearch:
        def search(self, query, **kwargs):
            return WebSearchResult(status="found", items=[WebEvidence(id=6,
                title="取消收费公告", url="https://www.hangzhou.gov.cn/ticket",
                content="灵隐寺原75元费用将全部取消，现免费开放。")])

    class OldPrice:
        def generate_json(self, messages):
            return '{"source_id":6,"amount":"75","evidence":"灵隐寺原75元费用将全部取消"}'

    assert lookup_price("杭州", "灵隐寺", None, CancelledSearch(), OldPrice()).amount is None


"""适用期测试函数：公告过期或尚未生效时，不展示成所选日期的可用公布价。"""

@pytest.mark.parametrize("field,value,status", [
    ("valid_until", "2026-09-01", "expired"),
    ("valid_from", "2026-11-01", "not_applicable"),
])
def test_price_validity(field, value, status):
    class Dated(Model):
        def generate_json(self, messages):
            return super().generate_json(messages).replace(
                f'"{field}":null', f'"{field}":"{value}"')
    result = lookup_price("杭州", "雷峰塔", date(2026, 10, 1), Search(), Dated())
    assert result.status == status and result.date_confirmed is False


"""官方来源测试函数：搜索返回伪官方域名也不能进入模型提取。"""

def test_unofficial_source_is_not_used():
    class Unofficial(Search):
        def search(self, query, **kwargs):
            found = super().search(query)
            found.items[0].url = "https://www.hangzhou.gov.cn.evil.example/ticket"
            return found
    assert lookup_price("杭州", "雷峰塔", None, Unofficial(), Model()).status == "unavailable"


"""优惠范围测试函数：儿童免票不取消成人基础票价。"""

def test_child_free_policy_does_not_erase_adult_price():
    class ChildPolicy(Search):
        def search(self, query, **kwargs):
            found = super().search(query)
            found.items[0].content = "雷峰塔成人门票40元/人，6周岁以下儿童免收门票。"
            return found

    class AdultPrice(Model):
        def generate_json(self, messages):
            return super().generate_json(messages).replace('"雷峰塔成人门票40元/人"',
                '"雷峰塔成人门票40元/人，6周岁以下儿童免收门票。"')

    assert lookup_price("杭州", "雷峰塔", None, ChildPolicy(), AdultPrice()).status == "published"


"""城市缓存测试函数：不同城市同名地点不得复用旧城市的官方来源。"""

def test_price_cache_is_not_shared_between_cities(monkeypatch):
    old = SimpleNamespace(destination="成都", ticket_prices=[PublishedPrice(
        place="人民公园", status="published", amount="20")])
    day = SimpleNamespace(date=None, activities=[SimpleNamespace(place=SimpleNamespace(
        map=SimpleNamespace(matched_name="人民公园")))])
    plan = SimpleNamespace(destination="上海", days=[day], model_copy=lambda update: update)
    calls = []

    def lookup(city, place, travel_date, web, model):
        calls.append(city)
        return PublishedPrice(place=place, status="unavailable")

    monkeypatch.setattr("app.services.travel_prices.lookup_price", lookup)
    result = add_prices(plan, old, None, None)
    assert calls == ["上海"] and result["ticket_prices"][0].amount is None
