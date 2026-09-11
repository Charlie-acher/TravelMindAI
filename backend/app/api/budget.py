"""HTTP 接口层：接收预算条件，调用预算服务并返回结果。"""

from fastapi import APIRouter, Request

from app.schemas.budget import BudgetInput, BudgetResponse, BudgetSummary
from app.services.budget_service import calculate_budget

router = APIRouter(prefix="/budget", tags=["预算"])

"""预算估算接口函数：接收旅行条件并返回费用明细。"""

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
