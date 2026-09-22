"""工作区业务层：汇总指定账号会话账目，最近输入与累计消耗分开计算。"""

from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Engine, cast, func, select

from app.config import Settings
from app.models.requirement_turn import RequirementTurn
from app.models.usage import UsageEvent

"""状态汇总函数：累计值在数据库聚合，最近调用仅限制展示数量，未知值保留为空。"""

def read_workspace_status(engine: Engine, user_id: UUID, session_id: UUID,
                          settings: Settings) -> dict[str, Any]:
    table, payload = UsageEvent, UsageEvent.payload_json
    where = [table.user_id == user_id, table.session_id == session_id]
    fields = {key: cast(payload[key].astext, BigInteger) for key in (
        "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens")}
    provider, model = payload["provider"].astext, payload["model"].astext
    cache_eligible = payload["kind"].astext.in_(["text", "tools", "vision"])
    with engine.connect() as connection:
        totals = connection.execute(select(func.count(), func.sum(fields["total_tokens"]),
            func.count().filter(fields["total_tokens"].is_(None)),
            func.sum(fields["input_tokens"]).filter(cache_eligible),
            func.sum(fields["cache_read_tokens"]).filter(cache_eligible),
            func.count().filter(cache_eligible,
                fields["cache_read_tokens"].is_(None) | fields["input_tokens"].is_(None)),
        ).where(*where)).one()
        groups = connection.execute(select(provider, model, func.count(),
            func.sum(fields["input_tokens"]), func.sum(fields["output_tokens"]),
            func.sum(fields["cache_read_tokens"]),
            func.count().filter(fields["total_tokens"].is_(None)),
        ).where(*where).group_by(provider, model).order_by(provider, model)).all()
        recent = connection.scalars(select(payload).where(*where).order_by(
            table.started_at.desc(), table.id).limit(20)).all()
        # 每个真实调用型号只取最近一次对话输入；标题和历史压缩另计累计值。
        contexts = connection.scalars(select(payload).where(*where,
            payload["kind"].astext.in_(["text", "tools"]),
            payload["purpose"].astext == "conversation",
        ).distinct(provider, model).order_by(provider, model, table.started_at.desc(),
                                            table.id)).all()
        historical = bool(connection.scalar(select(func.count()).select_from(
            RequirementTurn).where(RequirementTurn.session_id == session_id,
                ~select(table.id).where(*where,
                    table.message_id == RequirementTurn.message_id).exists())))
    context_views = []
    for call in sorted(contexts, key=lambda item: item["started_at"], reverse=True):
        capacity = settings.model_context_windows.get(call["model"])
        context_views.append({key: call.get(key) for key in (
            "provider", "model", "input_tokens", "started_at", "purpose")}
            | {"context_window": capacity,
               "ratio": call["input_tokens"] / capacity
                   if capacity and call.get("input_tokens") is not None else None})
    return {
        "total_calls": totals[0], "known_total_tokens": int(totals[1] or 0),
        "unknown_usage_calls": totals[2],
        "cache_hit_ratio": float(totals[4] / totals[3])
            if totals[3] and totals[4] is not None and not totals[5] else None,
        "latest_context": context_views[0] if context_views else None,
        "contexts": context_views,
        "models": [{"provider": row[0], "model": row[1], "calls": row[2],
                    "input_tokens": int(row[3]) if row[3] is not None else None,
                    "output_tokens": int(row[4]) if row[4] is not None else None,
                    "cache_read_tokens": int(row[5]) if row[5] is not None else None,
                    "unknown_usage_calls": row[6]} for row in groups],
        "recent_calls": [{key: call.get(key) for key in (
            "id", "provider", "model", "kind", "purpose", "state", "started_at",
            "input_tokens", "output_tokens", "elapsed_seconds")} for call in recent],
        "unattributed_history": historical,
    }
