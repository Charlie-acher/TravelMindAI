"""API 契约测试：模拟前端发 HTTP 请求，但不用真的占用网络端口。

TestClient 类似 Spring MockMvc：把请求交给完整应用，检查状态码和 JSON。
与领域测试不同，这里还验证 Pydantic 校验、序列化、路由和异常处理。
"""

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

"""每个用例独立创建应用，避免受电脑上临时设置的环境变量影响。

with 会负责客户端的打开与关闭；yield 把客户端交给当前测试使用。
"""

@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("TRAVELMIND_ENVIRONMENT", "test")
    with TestClient(create_app()) as test_client:
        yield test_client


"""金额使用字符串返回，前端不会因为 JSON 浮点数产生小数尾差。"""

def test_estimate_returns_traceable_decimal_strings(client: TestClient) -> None:
    response = client.post(
        "/api/v1/budget/estimate",
        json={"days": 3, "travelers": 2, "total_budget": "5000.00"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["budget"]["total"] == "2442.00"
    assert body["budget"]["remaining"] == "2558.00"
    assert body["budget"]["costs"]["accommodation"] == "400.00"
    assert body["budget"]["rooms"] == 1
    assert body["budget"]["nights"] == 2
    assert body["budget"]["price_version"] == "demo-cny-v1"
    assert body["budget"]["assumptions"]
    assert str(UUID(body["request_id"])) == response.headers["X-Request-ID"]


"""422表示请求字段不合法；通过同一套结构返回，而非泄露原始请求内容。"""

@pytest.mark.parametrize(
    "changes",
    [
        {"days": 1},
        {"days": 6},
        {"days": 2.5},
        {"days": True},
        {"travelers": 0},
        {"travelers": 9},
        {"travelers": False},
        {"total_budget": "0"},
        {"total_budget": "-1"},
        {"total_budget": "NaN"},
        {"total_budget": "Infinity"},
        {"total_budget": "0.001"},
        {"total_budget": "10000000000"},
        {"total_budget": True},
        {"lodging": "luxury"},
        {"unexpected": "value"},
    ],
)
def test_invalid_json_fields_use_one_error_contract(client: TestClient, changes: dict) -> None:
    payload = {"days": 3, "travelers": 2, "total_budget": "5000"} | changes
    response = client.post("/api/v1/budget/estimate", json=payload)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["retryable"] is False
    assert error["details"]["fields"]
    assert "input" not in error["details"]["fields"][0]
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


"""日期可以都不填；如果填写，就必须成对且与days一致，首尾日期都算一天。"""

@pytest.mark.parametrize(
    ("dates", "status"),
    [
        ({"start_date": "2026-10-01", "end_date": "2026-10-03"}, 200),
        ({"start_date": "2026-10-03", "end_date": "2026-10-01"}, 422),
        ({"start_date": "2026-10-01", "end_date": "2026-10-04"}, 422),
        ({"start_date": "2026-10-01"}, 422),
    ],
)
def test_optional_dates_must_agree_with_days(client: TestClient, dates: dict, status: int) -> None:
    response = client.post(
        "/api/v1/budget/estimate",
        json={"days": 3, "travelers": 2, "total_budget": "5000"} | dates,
    )
    assert response.status_code == status


"""超支不是接口故障，仍返回200，把差额告诉用户。"""

def test_over_budget_is_a_valid_business_result(client: TestClient) -> None:
    response = client.post(
        "/api/v1/budget/estimate",
        json={"days": 3, "travelers": 2, "total_budget": "2441.99"},
    )
    assert response.status_code == 200
    assert response.json()["budget"]["over_budget"] is True
    assert response.json()["budget"]["remaining"] == "-0.01"


"""M1-A 无外部依赖；健康检查和接口说明不应要求模型密钥或数据库。"""

def test_health_and_openapi_exist_without_external_services(client: TestClient) -> None:
    for path, status in [("live", "alive"), ("ready", "ready")]:
        response = client.get(f"/api/v1/health/{path}")
        assert response.status_code == 200
        assert response.json()["status"] == status
    schema = client.get("/openapi.json").json()
    assert "/api/v1/budget/estimate" in schema["paths"]
    assert client.get("/docs").status_code == 200


"""不存在的路由也遵守统一格式，前端只需一套错误处理。"""

def test_unknown_path_uses_same_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/not-found")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "HTTP_404"


"""注入领域拒绝场景，专门验证路由边界的400转换，不重复测试计算公式。"""

def test_domain_error_is_mapped_to_400(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import budget
    from app.services.budget_service import BudgetValidationError

    def reject(**kwargs: object) -> None:
        raise BudgetValidationError("预算规则不满足")

    monkeypatch.setattr(budget, "calculate_budget", reject)
    response = client.post(
        "/api/v1/budget/estimate",
        json={"days": 3, "travelers": 2, "total_budget": "5000"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BUDGET_INVALID"
    assert response.json()["error"]["message"] == "预算规则不满足"
