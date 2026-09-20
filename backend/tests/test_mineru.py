"""测试层：验证MinerU完整页处理、服务失败和可回查的原文依据。"""

import base64
import json
from io import BytesIO
from unittest.mock import Mock

import httpx
import pytest
from pypdf import PdfWriter

from app.services.attachment.storage import validate_upload

"""扫描上传测试函数：合法无文字层PDF应留给识别引擎，不能在上传时拒绝。"""

def test_scan_pdf_can_be_uploaded():
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.write(output)
    assert validate_upload(BytesIO(output.getvalue()), "扫描.pdf", "application/pdf")[1] == ".pdf"


"""服务协议测试函数：传入原件并请求全页，只接受完整成功的结构化结果。"""

def test_mineru_reads_all_pages_and_preserves_table():
    from app.services.attachment.mineru import MinerUClient

    payload = {"is_full_document": True, "pages": [
        {"page_idx": 0, "blocks": [{"type": "text", "content": "杭州西湖"}]},
        {"page_idx": 1, "blocks": [{"type": "table",
                                   "content": "<table><tr><td>断桥</td></tr></table>"}]},
    ]}
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["files"][0]["page_range"] == "all"
            assert body["ocr_mode"] == "ocr"
            assert base64.b64decode(body["files"][0]["source"]["data"]) == b"pdf"
            return httpx.Response(200, json={"job_id": "job-1", "status": "completed", "files": [{
                "status": "completed", "output_files": {"structured_content": {"file_id": "out-1"}},
            }]})
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = MinerUClient("http://127.0.0.1:8010", http).parse("攻略.pdf", b"pdf", 2)
    assert [s.page_number for s in result.sections] == [1, 2]
    assert "断桥" in result.sections[1].text


"""缺页测试函数：即使服务声称成功，也不能接受只解析了部分页面的结果。"""

@pytest.mark.parametrize("full,pages", [(False, [0, 1]), (True, [0]), (True, [0, 0])])
def test_missing_pages_are_rejected(full, pages):
    from app.services.attachment.mineru import MinerUError, parse_result

    with pytest.raises(MinerUError):
        parse_result({"is_full_document": full, "pages": [
            {"page_idx": i, "blocks": [{"type": "text", "content": "正文"}]} for i in pages
        ]}, 2)


"""部分失败测试函数：不能将失败任务降级成旧文字读取并标记成功。"""

def test_partial_job_fails():
    from app.services.attachment.mineru import MinerUClient, MinerUError

    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"job_id": "job-1", "status": "partial"}),
    )) as http, pytest.raises(MinerUError):
        MinerUClient("http://127.0.0.1:8010", http).parse("攻略.pdf", b"pdf", 2)


"""分批依据测试函数：长文按完整单元分批，每个地点必须来自引用的那一页。"""

def test_parsed_extraction_checks_page_evidence():
    from app.schemas.document.base import ParsedDocument, ParsedSection
    from app.services.attachment.analysis import AttachmentAnalysisError, analyze_parsed

    parsed = ParsedDocument(sections=[
        ParsedSection(text="杭州断桥", page_number=1, order=1),
        ParsedSection(text="白堤", page_number=2, order=2),
    ])
    model = Mock()
    model.generate_json.return_value = json.dumps({"summary": "路线", "waypoints": [
        {"name": "断桥", "evidence": "【第2页】杭州断桥"},
    ]})
    with pytest.raises(AttachmentAnalysisError, match="依据"):
        analyze_parsed(parsed, model)


"""长正文测试函数：最后一页也必须交给模型，不能只处理第一批。"""

def test_long_parsed_document_reads_last_page():
    from app.schemas.document.base import ParsedDocument, ParsedSection
    from app.services.attachment.analysis import analyze_parsed

    parsed = ParsedDocument(sections=[
        ParsedSection(text="普通正文" * 4000, page_number=1, order=1),
        ParsedSection(text="末页景点白堤", page_number=25, order=2),
    ])
    model = Mock()
    model.generate_json.return_value = '{"summary":"已读","waypoints":[]}'
    analyze_parsed(parsed, model)
    assert model.generate_json.call_count >= 2
    assert "【第25页】末页景点白堤" in model.generate_json.call_args.args[0][-1]["content"]


"""引用改写测试函数：名称确在原页时，从原文生成依据，不保存模型改写的署名。"""

def test_rewritten_caption_is_grounded_in_original_text():
    from app.schemas.document.base import ParsedDocument, ParsedSection
    from app.services.attachment.analysis import analyze_parsed, normalize_text

    text = "黄兴路步行街 by Minaki Saotome"
    parsed = ParsedDocument(sections=[ParsedSection(text=text, page_number=2, order=1)])
    model = Mock()
    model.generate_json.return_value = json.dumps({"summary": "长沙", "waypoints": [
        {"name": "黄兴路步行街", "evidence": "【第2页】黄兴路步行街 by Minaki_Saotome"},
    ]})
    point = analyze_parsed(parsed, model).waypoints[0]
    assert normalize_text(point.evidence.removeprefix("【第2页】")) in normalize_text(text)


"""图片补读测试函数：非连续PDF页应按实际页码送入视觉模型。"""

def test_selected_visual_pages_keep_physical_numbers(monkeypatch):
    from app.services.attachment.analysis import analyze_pdf_images

    monkeypatch.setattr("app.services.attachment.analysis.render_pdf_pages",
                        lambda content, pages: [b"image"] * len(pages))
    vision = Mock()
    vision.generate_images_json.return_value = json.dumps({"summary": "地图", "waypoints": [
        {"name": "断桥", "evidence": "【第25页】断桥"},
    ]})
    result = analyze_pdf_images(b"pdf", vision, [3, 25])
    assert result.waypoints[0].name == "断桥"
    assert "第3、25页" in result.summary and "全部" not in result.summary
    assert vision.generate_images_json.call_args.kwargs["page_numbers"] == [3, 25]


"""缓存重试测试函数：提取失败保留正文，第二次成功后不再调用解析或模型。"""

def test_reader_reuses_parsed_cache_on_retry():
    from uuid import uuid4

    from app.schemas.attachment import AttachmentAnalysis
    from app.schemas.document.base import ParsedDocument, ParsedSection
    from app.services.attachment.mineru import PARSER_VERSION
    from app.services.attachment.reader import AttachmentReader
    from tests.test_attachment_reader import uploaded

    sid = uuid4()
    item = uploaded(sid)
    item.file_name = "路线.docx"
    item.mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    storage, mineru, model = Mock(), Mock(), Mock()
    storage.get.return_value = item
    storage.read_content.return_value = (b"word", item.mime_type)
    parsed = ParsedDocument(sections=[ParsedSection(text="杭州断桥", order=1)])
    storage.read_parsed.side_effect = [None, parsed]
    mineru.parse.return_value = parsed

    def save(sid, aid, analysis, error):
        item.analysis, item.error_message = analysis, error
        return item

    storage.save_analysis.side_effect = save
    model.generate_json.side_effect = [
        "broken", AttachmentAnalysis(summary="杭州断桥").model_dump_json()]
    reader = AttachmentReader(storage, model, mineru=mineru)
    assert reader.read(sid, [item.id])[0].analysis is None
    assert reader.read(sid, [item.id])[0].analysis.parser_version == PARSER_VERSION
    reader.read(sid, [item.id])
    assert mineru.parse.call_count == 1
    assert model.generate_json.call_count == 2
    storage.save_parsed.assert_called_once()


"""数据库缓存测试函数：正文随本会话保存，旧结果升级且迟到旧结果不能覆盖新版。"""

def test_database_parsed_cache_and_upgrade(store_engine, tmp_path):
    from app.schemas.attachment import AttachmentAnalysis
    from app.schemas.document.base import ParsedDocument, ParsedSection
    from app.services.attachment.mineru import PARSER_VERSION
    from app.services.attachment.storage import AttachmentService
    from app.services.trip_service import TripService
    from tests.helpers import TEST_USER_ID

    service = AttachmentService(store_engine, tmp_path)
    sid = TripService(store_engine).create_session("MinerU测试", user_id=TEST_USER_ID).id
    item = service.upload(sid, BytesIO(b"original"), "a.txt", "text/plain")
    parsed = ParsedDocument(sections=[ParsedSection(text="第25页白堤", order=1, page_number=25)])
    service.save_parsed(sid, item.id, parsed, PARSER_VERSION)
    assert service.read_parsed(sid, item.id, PARSER_VERSION) == parsed
    assert service.read_parsed(sid, item.id, "old") is None
    old = AttachmentAnalysis(summary="旧结果")
    service.save_analysis(sid, item.id, old, None)
    new = AttachmentAnalysis(summary="新结果", parser_version=PARSER_VERSION)
    assert service.save_analysis(sid, item.id, new, None).analysis == new
    assert service.save_analysis(sid, item.id, old, None).analysis == new
