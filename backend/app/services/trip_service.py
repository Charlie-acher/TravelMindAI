"""
存储服务层：创建旅行会话，保存和读取不同版本的草稿。
"""

from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import sessionmaker

from app.models.trip import Itinerary, TravelRequest, TravelSession


class SessionNotFoundError(LookupError):
    """会话不存在异常类：表示找不到指定的旅行会话。"""


@dataclass(frozen=True)
class SavedDraft:
    """已保存草稿类：组合行程记录和它对应的需求记录。"""

    # 本草稿依据的旅行需求版本，可通过 request_json 读取天数、人数、预算等条件。
    requirement: TravelRequest
    # 行程版本，可读取版本号、状态和 itinerary_json；request_id 指向上面的需求。
    itinerary: Itinerary


class TripService:
    """旅行存储服务类：负责会话和草稿的数据库读写。"""

    """初始化方法：准备后续读写数据库所需的连接配置。"""

    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    """会话创建方法：检查标题并将新会话保存到数据库。"""

    def create_session(self, title: str) -> TravelSession:
        title = title.strip()
        if not 1 <= len(title) <= 200:
            raise ValueError("会话标题必须为1到200个字符")

        # begin() 管理完整事务：正常退出时 commit；抛异常时 rollback；最后关闭 Session。
        with self._sessions.begin() as unit:
            trip = TravelSession(title=title)
            unit.add(trip)
            unit.flush()  # 发出 INSERT，取得数据库生成的时间等字段，但此时还没有提交。
        # 走到 with 外面才表示提交成功；失败时异常会直接向调用方传播。
        return trip

    """会话查询方法：按编号读取旅行会话。"""

    def get_session(self, session_id: UUID) -> TravelSession | None:
        with self._sessions() as unit:
            return unit.get(TravelSession, session_id)


    """草稿保存方法：一起保存需求和行程，并生成新版本号。"""

    def save_draft(
        self,
        session_id: UUID,
        *,
        request_json: dict[str, JsonValue],
        itinerary_json: dict[str, JsonValue],
    ) -> SavedDraft:
        with self._sessions.begin() as unit:
            # 锁住当前会话行，直到提交/回滚。另一保存请求必须等这次事务完成后再取版本。
            # 锁只限制同一会话；其他会话仍能并行保存。使用 PostgreSQL 默认 READ COMMITTED。
            trip = unit.scalar(
                select(TravelSession).where(TravelSession.id == session_id).with_for_update()
            )
            if trip is None:
                raise SessionNotFoundError("旅行会话不存在，请先创建会话")

            # MAX 只查本会话。没有历史版本时返回 None，通过 or 0 从版本1开始。
            request_version = (
                unit.scalar(
                    select(func.max(TravelRequest.version)).where(
                        TravelRequest.session_id == session_id
                    )
                )
                or 0
            ) + 1
            itinerary_version = (
                unit.scalar(
                    select(func.max(Itinerary.version)).where(Itinerary.session_id == session_id)
                )
                or 0
            ) + 1

            # 深复制使快照与调用方传入的可变字典分离；返回对象不共享调用方的嵌套列表。
            requirement = TravelRequest(
                session_id=session_id, version=request_version, request_json=deepcopy(request_json)
            )
            unit.add(requirement)
            unit.flush()  # 先取得需求主键，供下面的行程外键引用；仍在同一个事务中。
            itinerary = Itinerary(
                session_id=session_id,
                request_id=requirement.id,
                version=itinerary_version,
                itinerary_json=deepcopy(itinerary_json),
            )
            unit.add(itinerary)
            unit.flush()  # 这里若违反约束，上面的需求 INSERT 也会回滚。

            # 使用数据库当前时刻更新最近修改时间；和两条快照一起提交或一起撤销。
            # clock_timestamp() 取实际执行时刻，包含等待行锁的时间。
            unit.execute(
                update(TravelSession)
                .where(TravelSession.id == session_id)
                .values(updated_at=func.clock_timestamp())
            )
        return SavedDraft(requirement=requirement, itinerary=itinerary)

    """草稿查询方法：读取最新或指定版本的草稿及对应需求。"""

    def get_draft(self, session_id: UUID, *, version: int | None = None) -> SavedDraft | None:
        query = (
            select(TravelRequest, Itinerary)
            .join(Itinerary, Itinerary.request_id == TravelRequest.id)
            .where(Itinerary.session_id == session_id)
        )
        if version is not None:
            query = query.where(Itinerary.version == version)
        with self._sessions() as unit:
            row = unit.execute(query.order_by(Itinerary.version.desc()).limit(1)).first()
            if row is None:
                return None
            return SavedDraft(requirement=row[0], itinerary=row[1])
