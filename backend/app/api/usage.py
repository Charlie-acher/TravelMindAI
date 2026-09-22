"""HTTP接口层：仅管理员读取调用费用账本，支持日期、请求编号和明细分页。"""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import require_admin
from app.services.usage_store import read_usage

router = APIRouter(prefix="/admin/usage", dependencies=[Depends(require_admin)], tags=["费用统计"])


"""费用读取函数：默认最近一天，时间必须带时区，最多查询31天。"""

@router.get("")
def usage(request: Request, start: datetime | None = None, end: datetime | None = None,
          request_id: str | None = Query(default=None, max_length=100),
          offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200)
          ) -> dict[str, Any]:
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(days=1)
    if not start.tzinfo or not end.tzinfo or not timedelta(0) < end - start <= timedelta(days=31):
        raise HTTPException(422, "请使用带时区的起止时间，范围不超过31天")
    if request.app.state.database_engine is None:
        raise HTTPException(503, "费用账本需要数据库")
    return read_usage(request.app.state.database_engine, start, end, request_id, offset, limit)
