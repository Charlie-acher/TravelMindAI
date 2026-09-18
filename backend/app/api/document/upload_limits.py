"""HTTP中间件层：在资料和私人附件接口处理文件前检查上传大小，拒绝超大请求。
由应用入口注册，位于请求编号处理之后、资料接口之前。
"""

import re

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# 文件最多20MiB，额外留64KiB给文件名和上传表单的分隔信息。
MAX_UPLOAD_BODY_BYTES = 20 * 1024 * 1024 + 64 * 1024
UPLOAD_TOO_LARGE = "上传请求过大，单份资料最多20 MiB，请减少文件或表单内容"
# 表单解析前按最大类型PDF放行，解析后再按真实文件类型检查各自上限。
MAX_ATTACHMENT_BODY_BYTES = 30_000_000 + 64 * 1024
ATTACHMENT_TOO_LARGE = "上传请求过大，私人PDF最多30 MB，其他附件最多10 MiB"


class DocumentUploadLimitMiddleware:
    """上传大小检查中间件类：按资料或附件的上限及时停止接收。"""

    """初始化函数：保存下一步要调用的应用处理程序。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    """请求处理函数：判断是否为资料上传，检查大小后交给后续程序处理。"""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        path = scope["path"].rstrip("/")
        if re.fullmatch(r"/api/v1/sessions/[^/]+/attachments", path):
            max_bytes, error_message = MAX_ATTACHMENT_BODY_BYTES, ATTACHMENT_TOO_LARGE
        elif path in {"/api/v1/documents", "/api/v1/admin/documents"}:
            max_bytes, error_message = MAX_UPLOAD_BODY_BYTES, UPLOAD_TOO_LARGE
        else:
            await self.app(scope, receive, send)
            return
        # 先检查浏览器声明的总大小；没有声明时，由下面的接收函数逐块统计。
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > max_bytes:
            # 请求编号已经生成，提前拒绝时也带上它，方便定位问题。
            response = JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "HTTP_413",
                        "message": error_message,
                        "retryable": False,
                        "details": {},
                    },
                    "request_id": scope["state"]["request_id"],
                },
            )
            await response(scope, receive, send)
            return
        received = 0

        """分块接收函数：累计实际收到的字节数，超过上限就报错停止。"""

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                # 按实际内容检查，不能只相信浏览器声明的大小；框架会清理临时文件。
                if received > max_bytes:
                    raise HTTPException(413, error_message)
            return message

        await self.app(scope, limited_receive, send)
