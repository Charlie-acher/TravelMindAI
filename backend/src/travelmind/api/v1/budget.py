"""预算路由，相当于 Java 的 BudgetController。

router 只是收集路由定义；把它挂载到 create_app() 创建的应用后才可对外访问。
导入本文件不会启动 HTTP 服务器，也不会立刻计算预算。
"""

from fastapi import APIRouter, Request

from travelmind.domain.budget import calculate_budget
from travelmind.schemas import BudgetInput, BudgetResponse, BudgetSummary

router = APIRouter(prefix="/budget", tags=["预算"])

"""
接收已校验的 DTO，调用纯计算函数，并返回结构化预算。

payload 由 FastAPI 自动从请求 JSON 构造，无需自己 json.loads。
request 是当前 HTTP 请求；state.request_id 由应用中间件提前填写。
response_model 决定响应结构和 OpenAPI 文档，也会校验返回的数据。
"""
@router.post("/estimate", response_model=BudgetResponse)
def estimate_budget(payload: BudgetInput, request: Request) -> BudgetResponse:
    estimate = calculate_budget(
        days=payload.days,
        travelers=payload.travelers,
        total_budget=payload.total_budget,
        lodging=payload.lodging,
    )
    return BudgetResponse(
        budget=BudgetSummary.model_validate(estimate),
        request_id=request.state.request_id,
    )
