"""HTTP接口层：保护私人附件上传、列表、详情和原件下载，调用附件存储服务。"""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile

from app.api.auth import require_session_owner
from app.schemas.attachment import AttachmentView
from app.services.attachment.storage import AttachmentService

router = APIRouter(prefix="/sessions", tags=["私人附件"],
                   dependencies=[Depends(require_session_owner)])


"""附件服务获取函数：使用会话专属子目录，原件不挂载为公开静态资源。"""

def get_attachment_service(request: Request) -> AttachmentService:
    engine = request.app.state.database_engine
    if engine is None:
        raise HTTPException(503, "未启用数据库，请配置后重启服务")
    return AttachmentService(
        engine, request.app.state.settings.document_upload_dir / "conversations",
    )


AttachmentDependency = Annotated[AttachmentService, Depends(get_attachment_service)]


"""上传接口函数：同步检查与保存，成功或失败都关闭临时上传文件。"""

@router.post("/{session_id}/attachments", response_model=AttachmentView, status_code=201)
def upload_attachment(session_id: UUID, file: UploadFile,
                      service: AttachmentDependency, response: Response) -> AttachmentView:
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.upload(session_id, file.file, file.filename or "", file.content_type or "")
    finally:
        file.file.close()


"""列表接口函数：只列出经过归属检查的会话附件。"""

@router.get("/{session_id}/attachments", response_model=list[AttachmentView])
def list_attachments(session_id: UUID, service: AttachmentDependency,
                     response: Response) -> list[AttachmentView]:
    response.headers["Cache-Control"] = "no-store"
    return service.list(session_id)


"""详情接口函数：返回本会话附件的原件信息和当前识别状态。"""

@router.get("/{session_id}/attachments/{attachment_id}", response_model=AttachmentView)
def get_attachment(session_id: UUID, attachment_id: UUID, service: AttachmentDependency,
                   response: Response) -> AttachmentView:
    response.headers["Cache-Control"] = "no-store"
    return service.get(session_id, attachment_id)


"""原件读取接口函数：认证后允许PDF及静态图片预览，其他类型仍下载。"""

@router.get("/{session_id}/attachments/{attachment_id}/content")
def download_attachment(session_id: UUID, attachment_id: UUID,
                        service: AttachmentDependency, preview: bool = False) -> Response:
    item = service.get(session_id, attachment_id)
    content, mime = service.read_content(session_id, attachment_id)
    disposition = "inline" if preview and mime in {
        "application/pdf", "image/png", "image/jpeg", "image/webp",
    } else "attachment"
    # PDF由浏览器内置查看器打开；sandbox会禁止该查看器加载插件。
    policy = ("default-src 'none'; object-src 'self'; frame-ancestors 'none'"
              if disposition == "inline" and mime == "application/pdf"
              else "sandbox; default-src 'none'; img-src 'self'")
    return Response(content=content, media_type=mime, headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": policy,
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(item.file_name, safe='')}",
    })
