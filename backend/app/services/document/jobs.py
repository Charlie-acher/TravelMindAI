"""资料任务业务层：持久化后台任务，用数据库锁识别中断并防止重复执行。"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import sessionmaker

from app.models.document import DocumentRecord
from app.models.document_job import DocumentJobRecord
from app.schemas.document.job import DocumentJobView
from app.schemas.document.search import IndexProgress


class DocumentJobService:
    """资料任务服务类：管理最近任务，实际解析和索引仍由原业务服务处理。"""

    """初始化函数：借用数据库连接池，保存服务端指定的归属。"""

    def __init__(self, engine: Engine, owner_id: str) -> None:
        self._engine = engine
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)
        self._owner_id = owner_id

    """执行锁函数：会话锁跨批次保留，进程退出后数据库自动释放。"""

    @contextmanager
    def _lock(self, identifier: UUID, wait: bool = False) -> Iterator[bool]:
        key = int.from_bytes(identifier.bytes[:8], "big", signed=True)
        with self._engine.connect() as connection:
            if wait:
                connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
                locked = True
            else:
                locked = bool(connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": key},
                ))
            connection.commit()
            try:
                yield locked
            finally:
                if locked:
                    # 显式释放会话锁，不能把仍持锁的连接放回连接池。
                    try:
                        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                        connection.commit()
                    except Exception:
                        connection.invalidate()
                        raise

    """任务读取函数：通过资料表核对归属，没有任务返回空值。"""

    def read(self, identifier: UUID) -> DocumentJobView | None:
        with self._sessions() as unit:
            if unit.scalar(select(DocumentRecord.id).where(
                DocumentRecord.id == identifier, DocumentRecord.owner_id == self._owner_id,
            )) is None:
                raise HTTPException(404, "资料不存在")
            row = unit.get(DocumentJobRecord, identifier)
            return DocumentJobView.model_validate(row) if row else None

    """任务提交函数：重复点击沿用运行任务，失败或暂停后显式提交才创建新执行编号。"""

    def start(self, identifier: UUID, kind: str) -> tuple[DocumentJobView, bool]:
        with self._lock(identifier) as locked:
            if not locked:
                current = self.read(identifier)
                if current and current.kind == kind and current.status in {"queued", "running"}:
                    return current, False
                raise HTTPException(409, "上一批仍在处理，请稍后重试")
            return self._start(identifier, kind)

    """任务保存函数：持执行锁时检查资料并提交新的任务状态。"""

    def _start(self, identifier: UUID, kind: str) -> tuple[DocumentJobView, bool]:
        if kind not in {"parse", "index"}:
            raise HTTPException(422, "未知资料任务类型")
        with self._sessions.begin() as unit:
            document = unit.scalar(select(DocumentRecord).where(
                DocumentRecord.id == identifier, DocumentRecord.owner_id == self._owner_id,
            ).with_for_update())
            if document is None:
                raise HTTPException(404, "资料不存在")
            if document.status == "deleting":
                raise HTTPException(409, "资料已停用，请完成删除")
            if kind == "index" and document.status != "parsed":
                raise HTTPException(409, "请先成功读取资料，再建立索引")
            row = unit.get(DocumentJobRecord, identifier)
            if row and row.status in {"queued", "running"}:
                if row.kind != kind:
                    raise HTTPException(409, "这份资料已有其他后台任务，请等待或暂停")
                return DocumentJobView.model_validate(row), False
            if row is None:
                row = DocumentJobRecord(document_id=identifier)
                unit.add(row)
            row.run_id, row.kind, row.status = uuid4(), kind, "queued"
            row.indexed, row.total, row.error_message = 0, 0, None
            unit.flush()
            return DocumentJobView.model_validate(row), True

    """暂停函数：保存暂停状态，已经开始的一批允许完成，后续批次不再启动。"""

    def pause(self, identifier: UUID) -> DocumentJobView | None:
        self.read(identifier)
        with self._sessions.begin() as unit:
            row = unit.get(DocumentJobRecord, identifier, with_for_update=True)
            if row and row.status in {"queued", "running"}:
                row.status = "paused"
                unit.flush()
            return DocumentJobView.model_validate(row) if row else None

    """任务执行函数：逐批运行并保存确认进度，失败只记录安全说明，不自动重试收费调用。"""

    def execute(
        self, identifier: UUID, run_id: UUID, step: Callable[[], IndexProgress | None],
    ) -> None:
        with self._lock(identifier, wait=True) as locked:
            if not locked:
                return
            with self._sessions.begin() as unit:
                row = unit.get(DocumentJobRecord, identifier, with_for_update=True)
                if row is None or row.run_id != run_id or row.status != "queued":
                    return
                row.status = "running"
            try:
                previous = -1
                while True:
                    current = self.read(identifier)
                    if current is None or current.run_id != run_id or current.status != "running":
                        return
                    progress = step()
                    with self._sessions.begin() as unit:
                        row = unit.get(DocumentJobRecord, identifier, with_for_update=True)
                        if row is None or row.run_id != run_id:
                            return
                        if progress is not None:
                            row.indexed, row.total = progress.indexed, progress.total
                        if row.status != "running":
                            return
                        if progress is None or progress.complete:
                            row.status = "completed"
                            return
                        if progress.indexed <= previous:
                            raise RuntimeError("索引没有新增确认进度")
                        previous = progress.indexed
            except Exception:
                # 不持久化外部异常原文，避免响应、连接地址或密钥进入页面。
                with self._sessions.begin() as unit:
                    row = unit.get(DocumentJobRecord, identifier, with_for_update=True)
                    if row and row.run_id == run_id and row.status == "running":
                        row.status = "failed"
                        row.error_message = (
                            "后台处理未完成，请检查资料及服务状态后重试；已存索引会保留。"
                        )

    """入口失败标记函数：只更新已退出回调对应的运行编号，不误伤新的重试或暂停。"""

    def mark_interrupted(self, identifier: UUID, run_id: UUID) -> None:
        with self._sessions.begin() as unit:
            row = unit.scalar(select(DocumentJobRecord).join(
                DocumentRecord, DocumentRecord.id == DocumentJobRecord.document_id,
            ).where(
                DocumentRecord.owner_id == self._owner_id,
                DocumentJobRecord.document_id == identifier, DocumentJobRecord.run_id == run_id,
                DocumentJobRecord.status.in_(["queued", "running"]),
            ).with_for_update())
            if row:
                row.status = "failed"
                row.error_message = "后台处理连接中断，请重试；已保存的正文、片段和索引会保留。"

    """中断恢复函数：只标记没有存活执行锁的遗留任务，不在启动时自动调用模型。"""

    def recover_interrupted(self) -> int:
        with self._sessions() as unit:
            identifiers = list(unit.scalars(select(DocumentJobRecord.document_id).join(
                DocumentRecord, DocumentRecord.id == DocumentJobRecord.document_id,
            ).where(
                DocumentRecord.owner_id == self._owner_id,
                DocumentJobRecord.status.in_(["queued", "running"]),
            )))
        count = 0
        for identifier in identifiers:
            with self._lock(identifier) as locked:
                if not locked:
                    continue
                with self._sessions.begin() as unit:
                    row = unit.get(DocumentJobRecord, identifier, with_for_update=True)
                    if row and row.status in {"queued", "running"}:
                        row.status = "failed"
                        row.error_message = (
                            "服务中断，后台任务未完成；请显式重试，已存索引会继续使用。"
                        )
                        count += 1
        return count
