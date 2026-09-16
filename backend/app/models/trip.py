"""
数据库模型层：定义旅行会话、需求和行程对应的数据库表。
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """数据库模型基类：汇总各个模型的表结构。"""


class TravelSession(Base):
    """旅行会话模型类：对应 sessions 表，保存会话基本信息。"""

    __tablename__ = "sessions"  # 数据库中的实际表名。
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_sessions_status"),
        Index("ix_sessions_user_updated", "user_id", "updated_at", "id"),
    )

    # 会话主键，Python 在 INSERT 时调用 uuid4 生成；不能写成 uuid4() 提前生成。
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # 每个会话必须属于注册账号；0010升级前先显式处理旧历史。
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_sessions_user"))
    # 预留给 LangGraph 的执行线程编号，每个会话独立；现在还没有检查点表。
    thread_id: Mapped[UUID] = mapped_column(default=uuid4, unique=True)
    # 会话标题，例如“杭州三日游”，最多200个字符。
    title: Mapped[str] = mapped_column(String(200))
    # active 表示使用中，archived 表示归档；数据库也检查允许值。
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    # 由数据库记录创建时刻，带时区；避免不同客户端时钟各自生成时间。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 最近修改时间，保存草稿时更新。
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TravelRequest(Base):
    """旅行需求模型类：对应 travel_requests 表，保存各版本的需求。"""

    __tablename__ = "travel_requests"
    __table_args__ = (
        UniqueConstraint("session_id", "version", name="uq_travel_requests_session_version"),
        # 为草稿的复合外键提供目标，保证需求 id 与 session_id 必须属于同一行。
        UniqueConstraint("id", "session_id", name="uq_travel_requests_id_session"),
        CheckConstraint("version >= 1", name="ck_travel_requests_version"),
        CheckConstraint("jsonb_typeof(request_json) = 'object'", name="ck_travel_requests_json"),
    )

    # 本次需求快照的主键；同一会话的不同版本有不同 id。
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # 所属会话，外键拒绝不存在的会话；没有设置自动级联删除。
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id", name="fk_requests_session"))
    # 版本号从1开始，由存储服务分配，同一会话内不能重复。
    version: Mapped[int]
    # 旅行条件 JSON 对象，例如天数、人数、目的地、预算；具体业务字段在入口校验。
    request_json: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    # 这一版本的创建时间，不随新版本变化。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Itinerary(Base):
    """行程模型类：对应 itineraries 表，保存各版本的行程。"""

    __tablename__ = "itineraries"
    __table_args__ = (
        UniqueConstraint("session_id", "version", name="uq_itineraries_session_version"),
        ForeignKeyConstraint(
            ["request_id", "session_id"],
            ["travel_requests.id", "travel_requests.session_id"],
            name="fk_itineraries_request_session",
        ),
        CheckConstraint("version >= 1", name="ck_itineraries_version"),
        CheckConstraint("status IN ('draft', 'confirmed')", name="ck_itineraries_status"),
        CheckConstraint("jsonb_typeof(itinerary_json) = 'object'", name="ck_itineraries_json"),
    )

    # 行程版本编号，用于识别具体草稿。
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # 所属会话；复合外键另外保证所引用的需求属于这个会话。
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", name="fk_itineraries_session")
    )
    # 所依据的 TravelRequest.id；不是 HTTP 请求跟踪编号 request_id。
    request_id: Mapped[UUID]
    # 当前会话内的行程版本号，从1开始，不能重复。
    version: Mapped[int]
    # draft 表示草稿，confirmed 表示已确认。
    status: Mapped[str] = mapped_column(String(20), server_default="draft")
    # 行程内容；数据库只检查它是否为 JSON 对象。
    itinerary_json: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    # 当前行程版本创建时刻，带时区。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
