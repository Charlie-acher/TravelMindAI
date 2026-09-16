"""HTTP接口层：启动、读取和暂停资料后台任务，任务内自行创建并关闭连接。"""

from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.llm.embeddings import EmbeddingClient
from app.schemas.document.job import DocumentJobRequest, DocumentJobView
from app.schemas.document.search import IndexProgress
from app.services.document.jobs import DocumentJobService
from app.services.document.search import DocumentSearchService
from app.services.document.service import DocumentService
from app.services.document.vector_store import MilvusStore
from app.services.knowledge_scope import KNOWLEDGE_SCOPE, require_knowledge_ready

router = APIRouter(prefix="/documents", tags=["资料后台处理"])


"""任务服务获取函数：使用应用数据库，归属只由后端提供。"""

def get_job_service(request: Request) -> DocumentJobService:
    require_knowledge_ready(request)
    engine = request.app.state.database_engine
    if engine is None:
        raise HTTPException(503, "未启用数据库，请配置后重启服务")
    service = DocumentJobService(engine, KNOWLEDGE_SCOPE)
    with request.app.state.document_job_recovery_lock:
        if not request.app.state.document_jobs_recovered:
            # 首次恢复只运行一次，不能把另一个请求刚排队的任务误标中断。
            service.recover_interrupted()
            request.app.state.document_jobs_recovered = True
        for identifier, run_id in list(request.app.state.document_job_failures):
            service.mark_interrupted(identifier, run_id)
            request.app.state.document_job_failures.discard((identifier, run_id))
    return service


JobDependency = Annotated[DocumentJobService, Depends(get_job_service)]


"""后台排队函数：等待空位时不占线程池，拿到空位后执行原有同步业务。"""

async def run_queued_job(request: Request, job: DocumentJobView) -> None:
    # 入队接口已经检查共享范围；数据库操作在下方线程内执行并纳入失败恢复。
    async with request.app.state.document_job_slots:
        try:
            await run_in_threadpool(
                process_document_job, request.app.state.database_engine,
                request.app.state.settings, job,
            )
        except Exception:
            # 入口借连接失败也要可恢复；数据库仍断线时，下一次任务请求补写失败状态。
            request.app.state.document_job_failures.add((job.document_id, job.run_id))
            try:
                await run_in_threadpool(get_job_service, request)
            except SQLAlchemyError:
                pass


"""后台处理函数：在响应返回后运行，不借用已关闭的上传文件或请求连接。"""

def process_document_job(
    engine: Engine, settings: Settings, job: DocumentJobView,
) -> None:
    jobs = DocumentJobService(engine, KNOWLEDGE_SCOPE)
    documents = DocumentService(engine, settings.document_upload_dir, KNOWLEDGE_SCOPE)
    # HTTP连接的生命周期覆盖整个后台任务，页面关闭不会关闭这些连接。
    with httpx.Client() as http:
        search: DocumentSearchService | None = None

        """处理步骤函数：解析只执行一次，索引按原有8段批次逐步确认。"""

        def step() -> IndexProgress | None:
            nonlocal search
            if job.kind == "parse":
                result = documents.retry_parse(job.document_id)
                if result.status != "parsed":
                    raise HTTPException(422, "资料仍无法读取，请查看资料详情中的原因")
                return None
            if search is None:
                documents.generate_chunks(job.document_id)
                search = DocumentSearchService(
                    engine, MilvusStore(settings, http), EmbeddingClient(settings, http),
                    KNOWLEDGE_SCOPE,
                )
            return search.index_batch(job.document_id)

        jobs.execute(job.document_id, job.run_id, step)


"""后台提交接口函数：先提交任务再返回202，重复请求只返回同一个执行编号。"""

@router.post("/{document_id}/job", response_model=DocumentJobView, status_code=202)
def start_job(
    document_id: UUID, body: DocumentJobRequest, tasks: BackgroundTasks,
    request: Request, service: JobDependency,
) -> DocumentJobView:
    job, scheduled = service.start(document_id, body.kind)
    if scheduled:
        tasks.add_task(run_queued_job, request, job)
    return job


"""后台状态接口函数：读取持久状态，没有任务时返回null，不触发模型请求。"""

@router.get("/{document_id}/job", response_model=DocumentJobView | None)
def read_job(document_id: UUID, service: JobDependency) -> DocumentJobView | None:
    return service.read(document_id)


"""后台暂停接口函数：允许当前批次完成，保存状态阻止后续处理。"""

@router.post("/{document_id}/job/pause", response_model=DocumentJobView | None)
def pause_job(document_id: UUID, service: JobDependency) -> DocumentJobView | None:
    return service.pause(document_id)
