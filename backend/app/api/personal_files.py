"""HTTP接口层：认证后分页查询个人文件，按原有身份读取详情或导出行程。"""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.auth import CurrentUser
from app.schemas.personal_files import FileKind, PersonalFileDetail, PersonalFilePage
from app.services.personal_files import PersonalFileService, itinerary_markdown

router = APIRouter(prefix="/personal-files", tags=["个人文件"])


"""文件服务获取函数：复用应用数据库连接，未配置数据库时返回明确错误。"""


def get_file_service(request: Request) -> PersonalFileService:
    engine = request.app.state.database_engine
    if engine is None:
        raise HTTPException(503, "未启用数据库，请配置后重启服务")
    return PersonalFileService(engine)


FileService = Annotated[PersonalFileService, Depends(get_file_service)]


"""文件列表接口函数：账号取自登录Cookie，客户端只控制会话和展示过滤条件。"""


@router.get("", response_model=PersonalFilePage)
def list_files(
    user: CurrentUser,
    service: FileService,
    response: Response,
    session_id: UUID | None = None,
    kind: FileKind | None = None,
    q: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
) -> PersonalFilePage:
    response.headers["Cache-Control"] = "no-store"
    return service.list(user.id, session_id=session_id, kind=kind, q=q, offset=offset, limit=limit)


"""文件详情接口函数：用文件编号查验归属，不能依赖列表曾经返回过该文件。"""


@router.get("/{kind}/{identifier}", response_model=PersonalFileDetail)
def get_file(
    kind: FileKind, identifier: UUID, user: CurrentUser, service: FileService, response: Response
) -> PersonalFileDetail:
    response.headers["Cache-Control"] = "no-store"
    return service.detail(user.id, kind, identifier)


"""行程下载接口函数：即时生成选定版本的Markdown，不在磁盘上保存重复文件。"""


@router.get("/itinerary/{identifier}/download")
def download_itinerary(identifier: UUID, user: CurrentUser, service: FileService) -> Response:
    detail = service.detail(user.id, "itinerary", identifier)
    filename = quote(detail.item.file_name, safe="")
    return Response(
        content=itinerary_markdown(detail),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        },
    )
