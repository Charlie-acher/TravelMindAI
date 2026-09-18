"""
存储服务层：创建旅行会话，保存和读取不同版本的草稿。
"""

import os
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import Engine, delete, func, select, tuple_, update
from sqlalchemy.orm import Session, sessionmaker

from app.models.attachment import ConversationAttachment
from app.models.requirement_turn import RequirementTurn
from app.models.trip import Itinerary, TravelRequest, TravelSession


class SessionNotFoundError(LookupError):
    """会话不存在异常类：表示找不到指定的旅行会话。"""


class AttachmentCleanupRecord(BaseModel):
    """原件清理记录类：保留已删除会话的归属和原件名，供文件占用后重试。"""

    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    user_id: UUID
    storage_names: list[str]


"""附件清理加锁函数：按会话编号取跨进程事务锁，统一先于会话行锁取得。"""

def lock_attachment_cleanup(unit: Session, session_id: UUID) -> None:
    digest = sha256(b"attachment-cleanup:" + session_id.bytes).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    unit.execute(select(func.pg_advisory_xact_lock(key)))


@dataclass(frozen=True)
class SavedDraft:
    """已保存草稿类：组合行程记录和它对应的需求记录。"""

    # 本草稿依据的旅行需求版本，可通过 request_json 读取天数、人数、预算等条件。
    requirement: TravelRequest
    # 行程版本，可读取版本号、状态和 itinerary_json；request_id 指向上面的需求。
    itinerary: Itinerary


"""事务内草稿保存函数：调用方先锁会话，将需求和行程加入同一笔事务。"""

def save_draft_in_transaction(
    unit: Session, session_id: UUID, *, request_json: dict[str, JsonValue],
    itinerary_json: dict[str, JsonValue],
) -> SavedDraft:
    request_version = (unit.scalar(select(func.max(TravelRequest.version)).where(
        TravelRequest.session_id == session_id,
    )) or 0) + 1
    itinerary_version = (unit.scalar(select(func.max(Itinerary.version)).where(
        Itinerary.session_id == session_id,
    )) or 0) + 1
    requirement = TravelRequest(
        session_id=session_id, version=request_version, request_json=deepcopy(request_json),
    )
    unit.add(requirement)
    unit.flush()  # 取得需求主键，仍未提交；后续写入失败时一起回滚。
    itinerary = Itinerary(
        session_id=session_id, request_id=requirement.id, version=itinerary_version,
        itinerary_json=deepcopy(itinerary_json),
    )
    unit.add(itinerary)
    unit.flush()
    return SavedDraft(requirement=requirement, itinerary=itinerary)


class TripService:
    """旅行存储服务类：负责会话和草稿的数据库读写。"""

    """初始化方法：准备后续读写数据库所需的连接配置。"""

    def __init__(self, engine: Engine, *, attachment_dir: Path | None = None) -> None:
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)
        self._attachment_dir = attachment_dir.resolve() if attachment_dir is not None else None

    """会话创建方法：检查标题并将新会话保存到数据库。"""

    def create_session(self, title: str, *, user_id: UUID) -> TravelSession:
        title = title.strip()
        if not 1 <= len(title) <= 200:
            raise ValueError("会话标题必须为1到200个字符")

        # begin() 管理完整事务：正常退出时 commit；抛异常时 rollback；最后关闭 Session。
        with self._sessions.begin() as unit:
            trip = TravelSession(title=title, user_id=user_id)
            unit.add(trip)
            unit.flush()  # 发出 INSERT，取得数据库生成的时间等字段，但此时还没有提交。
        # 走到 with 外面才表示提交成功；失败时异常会直接向调用方传播。
        return trip

    """会话查询方法：按编号读取旅行会话。"""

    def get_session(self, session_id: UUID) -> TravelSession | None:
        with self._sessions() as unit:
            return unit.get(TravelSession, session_id)

    """历史列表函数：只列出当前账号，以更新时间和编号作稳定分页。"""

    def list_sessions(
        self, user_id: UUID, limit: int, cursor: tuple[datetime, UUID] | None = None,
    ) -> list[TravelSession]:
        query = select(TravelSession).where(
            TravelSession.user_id == user_id, TravelSession.status == "active",
        )
        if cursor is not None:
            query = query.where(tuple_(TravelSession.updated_at, TravelSession.id) < cursor)
        with self._sessions() as unit:
            return list(unit.scalars(query.order_by(
                TravelSession.updated_at.desc(), TravelSession.id.desc(),
            ).limit(limit + 1)))


    """会话改名方法：事务内检查归属，只更新标题，不改变对话排序。"""

    def rename_session(self, session_id: UUID, title: str, *, user_id: UUID) -> TravelSession:
        title = title.strip()
        if not 1 <= len(title) <= 200:
            raise ValueError("会话标题必须为1到200个字符")
        with self._sessions.begin() as unit:
            trip = unit.scalar(select(TravelSession).where(
                TravelSession.id == session_id, TravelSession.user_id == user_id,
            ).with_for_update())
            if trip is None:
                raise SessionNotFoundError("旅行会话不存在")
            trip.title = title
            unit.flush()
        return trip

    """会话删除方法：锁定当前账号的会话，再按外键顺序一次删除全部历史。"""

    def delete_session(self, session_id: UUID, *, user_id: UUID) -> None:
        deleted = False
        with self._sessions.begin() as unit:
            lock_attachment_cleanup(unit, session_id)
            trip = unit.scalar(select(TravelSession).where(
                TravelSession.id == session_id, TravelSession.user_id == user_id,
            ).with_for_update())
            if trip is not None:
                names = list(unit.scalars(select(ConversationAttachment.storage_name).where(
                    ConversationAttachment.session_id == session_id,
                )))
                if names:
                    self._write_cleanup_record(AttachmentCleanupRecord(
                        session_id=session_id, user_id=user_id, storage_names=names,
                    ))
                # 追加、保存和撤销也先锁同一行，避免删除与在途提交交错留下残余。
                for model in (RequirementTurn, Itinerary, TravelRequest, ConversationAttachment):
                    unit.execute(delete(model).where(model.session_id == session_id))
                unit.delete(trip)
                deleted = True
        # 清理记录先于数据库提交落盘；回滚或文件占用时留下依据，不能静默丢弃失败。
        cleaned = self.cleanup_attachment_files(session_id, user_id=user_id)
        if not deleted and not cleaned:
            raise SessionNotFoundError("旅行会话不存在")

    """私有路径函数：只接受附件目录的直接文件，拒绝路径跳转和符号链接。"""

    def _private_attachment_path(self, name: str) -> Path:
        if self._attachment_dir is None:
            raise ValueError("删除附件会话必须配置原件目录")
        path = self._attachment_dir / name
        if path.is_symlink() or path.resolve().parent != self._attachment_dir:
            raise ValueError("附件清理路径无效")
        return path

    """原件路径函数：清理前一次核对所有文件名，只允许服务端UUID原件名。"""

    def _original_attachment_paths(self, names: list[str]) -> list[Path]:
        paths = []
        for name in names:
            if not re.fullmatch(
                r"[0-9a-f]{32}\.(png|jpg|jpeg|webp|pdf|docx|txt|md|markdown)", name,
            ):
                raise ValueError("附件原件名无效")
            paths.append(self._private_attachment_path(name))
        return paths

    """清理记录写入函数：会话锁内先落盘，再原子替换记录，成功后才能提交删除。"""

    def _write_cleanup_record(self, record: AttachmentCleanupRecord) -> None:
        self._original_attachment_paths(record.storage_names)
        path = self._private_attachment_path(f".delete-{record.session_id}.json")
        temporary = self._private_attachment_path(f".delete-{record.session_id}.tmp")
        with temporary.open("w", encoding="utf-8") as output:
            output.write(record.model_dump_json())
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)

    """原件恢复清理函数：会话已不存在且归属相符时执行，可由专用维护脚本免账号调用。"""

    def cleanup_attachment_files(self, session_id: UUID, *, user_id: UUID | None = None) -> bool:
        if self._attachment_dir is None:
            return False
        with self._sessions.begin() as unit:
            # 读取记录到删完记录全程持锁，防止旧清理覆盖再次删除写入的新原件名单。
            lock_attachment_cleanup(unit, session_id)
            path = self._private_attachment_path(f".delete-{session_id}.json")
            try:
                record = AttachmentCleanupRecord.model_validate_json(
                    path.read_text(encoding="utf-8"),
                )
            except FileNotFoundError:
                return False
            if record.session_id != session_id:
                raise ValueError("附件清理记录与会话编号不符")
            if user_id is not None and record.user_id != user_id:
                return False
            originals = self._original_attachment_paths(record.storage_names)
            # 记录可能来自失败事务；即便请求账号不同，也必须查整个会话而非按归属过滤。
            if unit.get(TravelSession, session_id) is not None:
                return False
            for original in originals:
                original.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
        return True

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

            saved = save_draft_in_transaction(
                unit, session_id, request_json=request_json, itinerary_json=itinerary_json,
            )

            # 使用数据库当前时刻更新最近修改时间；和两条快照一起提交或一起撤销。
            # clock_timestamp() 取实际执行时刻，包含等待行锁的时间。
            unit.execute(
                update(TravelSession)
                .where(TravelSession.id == session_id)
                .values(updated_at=func.clock_timestamp())
            )
        return saved

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
