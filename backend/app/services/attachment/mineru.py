"""附件解析接入层：调用本机MinerU，核对全文页数并整理可回查的正文。"""

import base64
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.schemas.document.base import ParsedDocument, ParsedSection
from app.services.chat.events import progress
from app.services.document.parser import MAX_SECTIONS, MAX_TEXT_CHARACTERS
from app.services.usage import usage_call

PARSER_VERSION = "mineru-4.0.4-ocr-v1"
MINERU_SUFFIXES = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp"}


class MinerUError(ValueError):
    """解析异常类：表示服务失败、缺页或结果超过可处理容量。"""


"""内容整理函数：保留文字、表格HTML及嵌套段落，不读取图片路径或二进制。"""

def block_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (block_text(item) for item in value)))
    if isinstance(value, dict):
        return "\n".join(filter(None, (block_text(value[key]) for key in
            ("content", "blocks", "text", "html", "captions", "footnotes") if key in value)))
    return ""


"""结果校验函数：拒绝部分结果和错页，PDF保留物理页码，Word不编造页码。"""

def parse_result(payload: dict[str, Any], expected_pages: int | None) -> ParsedDocument:
    pages = payload.get("pages")
    if payload.get("is_full_document") is not True or not isinstance(pages, list):
        raise MinerUError("MinerU未返回完整附件，请重试")
    if any(not isinstance(page, dict) or not isinstance(page.get("blocks"), list)
           or any(not isinstance(block, dict) for block in page["blocks"]) for page in pages):
        raise MinerUError("MinerU页面结构不完整，请检查解析服务后重试")
    if expected_pages is not None and [p.get("page_idx") for p in pages] != list(
        range(expected_pages)
    ):
        raise MinerUError("MinerU返回的页码不完整，附件尚未读完，请重试")
    sections: list[ParsedSection] = []
    warnings: list[str] = []
    total = 0
    for page in pages:
        page_number = page["page_idx"] + 1 if expected_pages is not None else None
        for block in page.get("blocks", []):
            text = block_text(block).strip()
            is_image = block.get("type") in {"image", "image_body"}
            if not text and is_image:
                text = "【图片区域，文字解析不能确定图中路线关系】"
            if not text:
                continue
            total += len(text)
            if total > MAX_TEXT_CHARACTERS or len(sections) >= MAX_SECTIONS:
                raise MinerUError("附件正文超过100万字符或5000段，请拆分后上传")
            sections.append(ParsedSection(text=text, page_number=page_number,
                section_path=["图片"] if is_image else [], order=len(sections) + 1))
    if not sections:
        raise MinerUError("MinerU未识别出可读内容，请检查是否为空白或模糊附件")
    return ParsedDocument(sections=sections, warnings=warnings)


class MinerUClient:
    """MinerU客户端类：向已配置服务提交原件，等待完整结果并限制等待时间。"""

    """初始化方法：借用HTTP连接，设置独立于聊天模型的解析期限。"""

    def __init__(self, base_url: str, http: httpx.Client, timeout: float = 600) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.timeout = timeout

    """解析方法：请求所有页面；部分失败不回退为旧解析成功，失败原件由上层保留。"""

    def parse(self, name: str, content: bytes, expected_pages: int | None) -> ParsedDocument:
        deadline = time.monotonic() + self.timeout
        job_id: str | None = None

        """请求函数：每次HTTP请求使用剩余期限，拒绝服务错误与重定向。"""

        def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MinerUError("MinerU读取超时，原件已保留，请稍后重试")
            response = self.http.request(method, self.base_url + path,
                timeout=min(60, remaining), follow_redirects=False, **kwargs)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or "error" in data:
                raise MinerUError("MinerU返回了无效结果，请检查服务后重试")
            return data

        with usage_call("mineru", "local-parse", self.base_url, "ocr") as call:
            call["units"] = expected_pages
            try:
                entry: dict[str, Any] = {"source": {"type": "inline",
                    "name": "attachment" + Path(name).suffix.lower(),
                    "data": base64.b64encode(content).decode("ascii")}}
                if expected_pages is not None:
                    entry["page_range"] = "all"
                job = request("POST", "/v1/parse/jobs", json={"files": [entry],
                    "tier": "flash" if Path(name).suffix.lower() == ".docx" else "standard",
                    # 旧攻略PDF可能有错码文字层；按实际页面OCR，避免再次信任损坏文字。
                    "ocr_mode": "auto" if Path(name).suffix.lower() == ".docx" else "ocr",
                    "output_formats": ["structured_content"]})
                job_id = str(job["job_id"])
                while job["status"] in {"queued", "running"}:
                    progress("attachment", "MinerU正在识别附件正文与表格，请稍候")
                    time.sleep(min(2, max(0, deadline - time.monotonic())))
                    job = request("GET", "/v1/parse/jobs/" + quote(job_id, safe=""))
                if job["status"] != "completed" or len(job["files"]) != 1:
                    raise MinerUError("MinerU未完成整份附件的识别，请检查服务后重试")
                result = job["files"][0]
                if result["status"] != "completed":
                    raise MinerUError("MinerU附件解析失败，原件已保留，请重试")
                identifier = result["output_files"]["structured_content"]["file_id"]
                parsed = parse_result(request("GET", "/v1/files/" + quote(identifier, safe="")
                    + "/content"), expected_pages)
                if Path(name).suffix.lower() == ".docx" and any(
                    section.section_path == ["图片"] for section in parsed.sections
                ):
                    parsed.warnings.append("DOCX含嵌入图片，当前保留图片位置，尚未执行图片视觉补读。")
                return parsed
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
                if isinstance(error, MinerUError):
                    raise
                raise MinerUError(
                    "MinerU服务不可用或结果不完整，请检查本机解析服务后重试") from error
            finally:
                # 取消仍在运行的超时任务；完成任务无需取消。外部错误不覆盖原识别错误。
                if job_id is not None and time.monotonic() >= deadline:
                    try:
                        self.http.post(self.base_url + "/v1/parse/jobs/" + quote(job_id, safe="")
                            + "/cancel", timeout=5)
                    except httpx.HTTPError:
                        pass
