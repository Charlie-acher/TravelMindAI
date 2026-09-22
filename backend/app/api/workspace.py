"""HTTP接口层：鉴权后返回当前账号指定会话的真实调用统计。"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.auth import CurrentUser, require_session_owner
from app.services.workspace_status import read_workspace_status

router = APIRouter(prefix="/sessions", tags=["对话工作区"],
                   dependencies=[Depends(require_session_owner)])


"""状态读取函数：会话所有者检查与历史接口一致，管理员不越权。"""

@router.get("/{session_id}/workspace-status")
def workspace_status(session_id: UUID, request: Request, user: CurrentUser) -> dict[str, Any]:
    return read_workspace_status(request.app.state.database_engine, user.id, session_id,
                                  request.app.state.settings)
