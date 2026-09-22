"""费用集成测试层：用隔离数据库验证记账幂等、完整汇总和管理员权限。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from app.services.auth import AuthService
from app.services.usage import usage_call, usage_scope
from app.services.usage_store import read_usage, save_usage

"""汇总测试函数：开始与完成只有一笔，跨币种分开合计，分页不截断总额。"""

def test_ledger_upsert_and_full_totals(store_engine):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    with usage_scope("ledger-case", lambda call: save_usage(store_engine, call)):
        with usage_call("kimi", "kimi-k2.6", "api.moonshot.cn", "text") as call:
            pending = read_usage(store_engine, start, datetime.now(timezone.utc))
            assert pending["items"][0]["state"] == "running"
            call.update(input_tokens=1000, output_tokens=100, cache_read_tokens=800)
        with usage_call("tavily", "basic", "api.tavily.com", "search") as call:
            call["units"] = 1
        with usage_call("baidu", "route", "mcp.map.baidu.com", "map"):
            pass
    report = read_usage(store_engine, start, datetime.now(timezone.utc), limit=1)
    assert report["total_calls"] == 3 and len(report["items"]) == 1
    assert Decimal(report["known_costs"]["CNY"]) == Decimal("0.00504")
    assert Decimal(report["known_costs"]["USD"]) == Decimal("0.008")
    assert report["status_counts"]["unknown"] == 1 and not report["complete"]
    assert read_usage(store_engine, start, datetime.now(timezone.utc), "other")["total_calls"] == 0


"""权限测试函数：普通用户不能读取其他人的费用，管理员使用真实登录查询。"""

def test_usage_api_is_admin_only(store_engine):
    service = AuthService(store_engine)
    service.create_user("usage-user", "test-pass123", "user")
    service.create_user("usage-admin", "test-pass123", "admin")
    settings = Settings(environment="test", database_url=SecretStr(
        store_engine.url.render_as_string(hide_password=False)))
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/admin/usage").status_code == 401
        for name, expected in (("usage-user", 403), ("usage-admin", 200)):
            response = client.post("/api/v1/auth/login", json={
                "username": name, "password": "test-pass123"},
                headers={"X-Requested-With": "TravelMindAI"})
            assert response.status_code == 200
            assert client.get("/api/v1/admin/usage").status_code == expected
        assert client.get("/api/v1/admin/usage", params={"start": "2026-09-01"}).status_code == 422
