"""费用存储层：保存独立账目并按时间汇总，写入故障明确记录而不覆盖业务异常。"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, Numeric, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.models.usage import UsageEvent

logger = logging.getLogger(__name__)


"""账目保存函数：调用编号唯一，重复写入不会重复计费，正文不进入表中。"""

def save_usage(engine: Engine | None, call: dict[str, Any]) -> None:
    if engine is None:
        call["persisted"] = False
        return
    try:
        with engine.begin() as connection:
            statement = insert(UsageEvent).values(id=UUID(call["id"]),
                request_id=call["request_id"],
                user_id=UUID(call["user_id"]) if call.get("user_id") else None,
                session_id=UUID(call["session_id"]) if call.get("session_id") else None,
                message_id=UUID(call["message_id"]) if call.get("message_id") else None,
                started_at=datetime.fromisoformat(call["started_at"]),
                payload_json={**call, "persisted": True})
            connection.execute(statement.on_conflict_do_update(
                index_elements=[UsageEvent.id],
                set_={"payload_json": statement.excluded.payload_json}))
        call["persisted"] = True
    except SQLAlchemyError:
        call["persisted"] = False
        logger.error("费用记录保存失败 request_id=%s call_id=%s", call["request_id"], call["id"])


"""账目查询函数：合计不受明细分页影响，不合并不同币种，未知项单列。"""

def read_usage(engine: Engine, start: datetime, end: datetime, request_id: str | None = None,
               offset: int = 0, limit: int = 100) -> dict[str, Any]:
    table = UsageEvent
    where = [table.started_at >= start, table.started_at < end]
    if request_id:
        where.append(table.request_id == request_id)
    cost = table.payload_json["cost"]
    currency, amount, status = cost["currency"].astext, cost["amount"].astext, cost["status"].astext
    with engine.connect() as connection:
        count = connection.scalar(select(func.count()).select_from(table).where(*where)) or 0
        sums = connection.execute(select(currency,
            func.sum(cast(amount, Numeric))).where(
                *where, amount.is_not(None)).group_by(currency))
        totals = {str(unit): str(total or Decimal(0)) for unit, total in sums}
        states = {str(state): int(number) for state, number in connection.execute(select(
            status, func.count()).where(*where).group_by(status))}
        rows = connection.scalars(select(table.payload_json).where(*where).order_by(
            table.started_at.desc(), table.id).offset(offset).limit(limit)).all()
    return {"total_calls": count, "known_costs": totals, "status_counts": states,
            "complete": not states.get("unknown", 0), "items": rows, "offset": offset,
            "limit": limit, "basis": "官方原价估算，非实际扣款；不含本机运行成本和套餐优惠"}
