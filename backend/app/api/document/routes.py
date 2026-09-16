"""
HTTP接口层：接收前端的资料上传和查询请求，调用资料业务服务并返回结果。
这些同步接口由FastAPI在线程池中执行，避免读文件时阻塞其他异步请求。
"""

from typing import Annotated
from uuid import UUID

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.api.document.jobs import get_job_service, run_queued_job
from app.schemas.document.base import (
    DocumentChunkPage,
    DocumentDetail,
    DocumentMetadata,
    DocumentSummary,
    DocumentUploadResult,
)
from app.services.document.metadata import infer_metadata
from app.services.document.service import DocumentService
from app.services.document.vector_store import MilvusStore

router = APIRouter(prefix="/documents", tags=["旅行资料"])


"""资料服务获取函数：读取应用的数据库和目录配置，创建接口需要的资料服务。"""


def get_document_service(request: Request) -> DocumentService:
    engine: Engine | None = request.app.state.database_engine
    if engine is None:
        raise HTTPException(503, "未启用数据库，请配置数据库后重启服务")
    # 当前由后端固定使用本地演示归属，前端不能传入其他用户的归属。
    return DocumentService(engine, request.app.state.settings.document_upload_dir, "local-demo")


# 接口参数使用这个类型时，FastAPI会先调用上面的函数准备资料服务。
DocumentDependency = Annotated[DocumentService, Depends(get_document_service)]


"""资料上传接口函数：接收文件，调用上传服务，返回资料详情和是否重复。"""


@router.post("", response_model=DocumentUploadResult, status_code=201)
def upload_document(
    file: UploadFile,
    response: Response,
    service: DocumentDependency,
    request: Request,
    tasks: BackgroundTasks,
    auto_process: bool = False,
) -> DocumentUploadResult:
    try:
        result = service.upload(file.file, file.filename or "", file.content_type or "")
    finally:
        # 处理结束后关闭上传临时文件，出错时也要关闭。
        file.file.close()
    # 新档案返回201，重复资料返回200；正文是否读取成功要看document.status。
    if result.duplicate:
        response.status_code = 200
    if auto_process and result.document.status == "parsed":
        try:
            jobs = get_job_service(request)
            job, scheduled = jobs.start(result.document.id, "index")
            if scheduled:
                tasks.add_task(run_queued_job, request, job)
        except (HTTPException, SQLAlchemyError):
            # 文件已落库，排队失败只提示处理状态，避免用户把整份文件当作上传失败。
            result.processing_error = "资料已保存，自动处理未提交；请打开资料重试后台建立索引。"
    return result


"""资料列表接口函数：接收文件名关键词和分页条件，返回当前归属内的资料摘要。"""


@router.get("", response_model=list[DocumentSummary])
def list_documents(
    service: DocumentDependency,
    metadata: Annotated[DocumentMetadata, Depends()],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query(max_length=255)] = "",
) -> list[DocumentSummary]:
    return service.list(limit, offset, q, metadata)


"""标签修改接口函数：按资料归属更新人工标签，未传字段保留，显式空值清除。"""

@router.patch("/{document_id}/metadata", response_model=DocumentDetail)
def update_document_metadata(
    document_id: UUID, body: DocumentMetadata, service: DocumentDependency,
) -> DocumentDetail:
    return service.update_metadata(document_id, body)


"""标签补全接口函数：识别已有资料，仅填数据库中仍为空的字段，保留人工填写值。"""

@router.post("/{document_id}/metadata/prefill", response_model=DocumentDetail)
def prefill_document_metadata(document_id: UUID, service: DocumentDependency) -> DocumentDetail:
    document = service.read(document_id)
    return service.update_metadata(
        document_id, infer_metadata(document.file_name, document.sections), fill_missing=True,
    )


"""资料详情接口函数：接收资料编号，返回已保存的正文和读取状态。"""


@router.get("/{document_id}", response_model=DocumentDetail)
def read_document(document_id: UUID, service: DocumentDependency) -> DocumentDetail:
    return service.read(document_id)


"""重新解析接口函数：重读已保存的失败资料，返回最新状态和原因，不调用模型。"""

@router.post("/{document_id}/retry", response_model=DocumentDetail)
def retry_document(document_id: UUID, service: DocumentDependency) -> DocumentDetail:
    return service.retry_parse(document_id)


"""资料删除接口函数：按归属停用并清理资料，重复请求继续清理或直接返回成功。"""

@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: UUID, request: Request, service: DocumentDependency,
) -> Response:
    with httpx.Client() as http:
        """向量清理函数：只有资料存在片段时才读取向量配置，不调用Embedding或大模型。"""

        def remove_vectors(owner_id: str, identifier: UUID) -> None:
            MilvusStore(request.app.state.settings, http).delete_document(owner_id, identifier)

        service.delete(document_id, remove_vectors)
    return Response(status_code=204)


"""片段生成接口函数：为已读取资料保存片段，重复调用返回同一批片段的首页。"""


@router.post("/{document_id}/chunks", response_model=DocumentChunkPage)
def generate_document_chunks(document_id: UUID, service: DocumentDependency) -> DocumentChunkPage:
    return service.generate_chunks(document_id)


"""片段列表接口函数：按资料编号和分页条件，返回已保存的片段及原文位置。"""


@router.get("/{document_id}/chunks", response_model=DocumentChunkPage)
def list_document_chunks(
    document_id: UUID,
    service: DocumentDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentChunkPage:
    return service.list_chunks(document_id, limit, offset)
