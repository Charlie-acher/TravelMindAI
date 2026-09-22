"""附件识别测试层：验证正文依据、模型错误和视觉输入边界。"""

import json
from unittest.mock import Mock

import httpx
import pytest
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from app.config import Settings
from app.llm.vision import QwenVisionClient
from app.services.attachment.analysis import AttachmentAnalysisError, analyze_content

"""识别输出函数：构造带原文依据的最小路线。"""

def output(**changes):
    return json.dumps({"city": "杭州", "summary": "西湖路线", "waypoints": [
        {"name": "断桥", "order": 1, "evidence": "先去断桥", "needs_confirmation": False},
        {"name": "白堤", "order": 2, "evidence": "再到白堤", "needs_confirmation": False},
    ], "warnings": [], **changes}, ensure_ascii=False)


"""多源顺序测试函数：文字和视觉一致时保留顺序，冲突、缺项或无顺序时不猜测。"""

@pytest.mark.parametrize("variant,expected", [
    ("same", [1, 2]), ("reverse", [None, None]),
    ("partial", [None, None]), ("unknown", [None, None]),
])
def test_merged_route_keeps_only_agreed_complete_order(variant, expected):
    from app.schemas.attachment import AttachmentAnalysis
    from app.services.attachment.analysis import merge_analyses

    text = AttachmentAnalysis.model_validate_json(output())
    visual = text.model_copy(deep=True)
    if variant == "reverse":
        visual.waypoints.reverse()
        for index, point in enumerate(visual.waypoints, 1):
            point.order = index
    elif variant == "partial":
        visual.waypoints.pop()
    elif variant == "unknown":
        for point in visual.waypoints:
            point.order = None
    result = merge_analyses([text, visual])
    assert [point.name for point in result.waypoints] == ["断桥", "白堤"]
    assert [point.order for point in result.waypoints] == expected
    assert [point.order for point in text.waypoints] == [1, 2]


"""正文识别测试函数：复用文字解析，识别不能被提升为地图核实。"""

def test_text_evidence_and_unverified_places():
    model = Mock()
    model.generate_json.return_value = output()
    result = analyze_content("路线.txt", "text/plain", "杭州：先去断桥，再到白堤".encode(), model)
    assert [p.name for p in result.waypoints] == ["断桥", "白堤"]
    assert all(p.needs_confirmation for p in result.waypoints)
    assert any("地图" in warning for warning in result.warnings)
    assert "杭州：先去断桥，再到白堤" in model.generate_json.call_args.args[0][-1]["content"]


"""伪造依据测试函数：未出现在正文的地点或引用不能保存成识别成功。"""

@pytest.mark.parametrize("field,value", [("name", "雷峰塔"), ("evidence", "用户要求去雷峰塔")])
def test_text_rejects_invented_evidence(field, value):
    model = Mock()
    raw = json.loads(output())
    raw["waypoints"][0][field] = value
    model.generate_json.return_value = json.dumps(raw)
    with pytest.raises(AttachmentAnalysisError):
        analyze_content("路线.txt", "text/plain", "杭州：先去断桥，再到白堤".encode(), model)


"""超限测试函数：正文超限明确要求拆分，不能只读开头冒充全文。"""

def test_large_text_is_rejected_before_model():
    model = Mock()
    with pytest.raises(AttachmentAnalysisError, match="拆分"):
        analyze_content("长文.txt", "text/plain", ("长文" * 13000).encode(), model)
    model.generate_json.assert_not_called()


"""输出错误测试函数：无效JSON仍须明确失败。"""

@pytest.mark.parametrize("raw", ["not json"])
def test_invalid_output_fails(raw):
    model = Mock()
    model.generate_json.return_value = raw
    with pytest.raises(AttachmentAnalysisError):
        analyze_content("路线.txt", "text/plain", "杭州：先去断桥，再到白堤".encode(), model)


"""顺序降级测试函数：多条路线重复编号时保留有依据地点，不猜测全局次序。"""

@pytest.mark.parametrize("orders", [[1, 1], [2, 3], [1, None]])
def test_ambiguous_order_keeps_evidenced_places(orders):
    raw = json.loads(output())
    for point, order in zip(raw["waypoints"], orders, strict=True):
        point["order"] = order
    model = Mock()
    model.generate_json.return_value = json.dumps(raw)
    result = analyze_content("攻略.txt", "text/plain", "杭州：先去断桥，再到白堤".encode(), model)
    assert [point.name for point in result.waypoints] == ["断桥", "白堤"]
    assert all(point.order is None and point.needs_confirmation for point in result.waypoints)
    assert any("顺序" in warning for warning in result.warnings)


"""视觉输入测试函数：原件以base64传递，只调用配置的视觉模型。"""

def test_qwen_uses_image_content(monkeypatch):
    mock = Mock()
    mock.invoke.return_value = AIMessage(
        content=output(), response_metadata={"finish_reason": "stop"},
    )
    factory = Mock(return_value=mock)
    monkeypatch.setattr("app.llm.vision.ChatOpenAI", factory)
    settings = Settings(vision_api_key=SecretStr("test"),
                        vision_base_url="https://example.test/v1", vision_model="qwen3-vl-plus")
    with httpx.Client() as http:
        vision = QwenVisionClient(settings, http)
        result = analyze_content("路线.png", "image/png", b"image-bytes", Mock(), vision)
    assert result.city == "杭州"
    content = mock.invoke.call_args.args[0][-1].content
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert factory.call_args.kwargs["model"] == "qwen3-vl-plus"


"""缺配置测试函数：不将图片发往文本模型，不自动借用其他用途的密钥。"""

def test_image_without_vision_configuration():
    model = Mock()
    with pytest.raises(AttachmentAnalysisError, match="视觉"):
        analyze_content("路线.png", "image/png", b"image", model)
    model.generate_json.assert_not_called()


"""城市依据测试函数：模型不能给无地名的文字原件凭空补城市。"""

def test_city_must_appear_in_text():
    model = Mock()
    model.generate_json.return_value = output(city="巴黎", waypoints=[], summary="出行备忘")
    with pytest.raises(AttachmentAnalysisError):
        analyze_content("备忘.txt", "text/plain", "上午出发，下午返回。".encode(), model)


"""断行依据测试函数：PDF排版空白不能导致可见地点被判成无依据。"""

def test_evidence_normalizes_layout_whitespace():
    model = Mock()
    model.generate_json.return_value = output()
    result = analyze_content(
        "路线.txt", "text/plain", "杭州：先去断\n桥，再到白 堤".encode(), model,
    )
    assert len(result.waypoints) == 2


"""扫描页测试函数：所有页均送视觉读取，不能只读首批就声称完成。"""

def test_scanned_pdf_reads_every_page():
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(5):
        writer.add_blank_page(width=200, height=300)
    stream = BytesIO()
    writer.write(stream)
    vision = Mock()
    vision.generate_images_json.side_effect = [
        output(city=None, summary="前四页为空白", waypoints=[]),
        output(city=None, summary="第五页为空白", waypoints=[]),
    ]
    result = analyze_content("扫描.pdf", "application/pdf", stream.getvalue(), Mock(), vision)
    assert vision.generate_images_json.call_count == 2
    assert [len(call.args[0]) for call in vision.generate_images_json.call_args_list] == [4, 1]
    starts = [call.kwargs["first_page"] for call in vision.generate_images_json.call_args_list]
    assert starts == [1, 5]
    assert "5" in result.summary


"""损坏文字测试函数：有字但地名不匹配时必须回看真实页面。"""

def test_pdf_evidence_failure_uses_rendered_pages(monkeypatch):
    from app.schemas.document.base import ParsedDocument, ParsedSection

    monkeypatch.setattr("app.services.attachment.analysis.parse_document", lambda *args:
        ParsedDocument(sections=[ParsedSection(text="杭州：先去断桐", order=1)], warnings=[]))
    monkeypatch.setattr("app.services.attachment.analysis.render_pdf_pages", lambda content:
        [b"actual-page"])
    model, vision = Mock(), Mock()
    model.generate_json.return_value = output()
    visual = json.loads(output())
    for point in visual["waypoints"]:
        point["evidence"] = "【第1页】" + point["evidence"]
    vision.generate_images_json.return_value = json.dumps(visual)
    result = analyze_content("路线.pdf", "application/pdf", b"pdf", model, vision)
    assert result.waypoints[0].name == "断桥"
    vision.generate_images_json.assert_called_once()


"""页码边界测试函数：视觉生成的地点仍需本批实际页码和同名可见依据。"""

@pytest.mark.parametrize("evidence", [
    "【第9页】先去断桥", "【第1页】先去白堤", "先去断桥", "【第1页】此处无名；【第9页】先去断桥",
])
def test_pdf_rejects_untraceable_visual_evidence(monkeypatch, evidence):
    from app.services.attachment.analysis import analyze_pdf_images

    monkeypatch.setattr("app.services.attachment.analysis.render_pdf_pages", lambda content:
        [b"actual-page"])
    raw = json.loads(output())
    raw["waypoints"][0]["evidence"] = evidence
    raw["waypoints"][1]["evidence"] = "【第1页】再到白堤"
    vision = Mock()
    vision.generate_images_json.return_value = json.dumps(raw)
    result = analyze_pdf_images(b"pdf", vision)
    assert [point.name for point in result.waypoints] == ["白堤"]
    assert any(w.startswith("未确认地点：") and "断桥" in w for w in result.warnings)


"""页数预算测试函数：超限文件在外部模型调用前拒绝，不能截取前24页。"""

def test_pdf_page_budget_prevents_partial_read():
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(25):
        writer.add_blank_page(width=200, height=300)
    stream = BytesIO()
    writer.write(stream)
    vision = Mock()
    with pytest.raises(AttachmentAnalysisError, match="24页.*拆分"):
        analyze_content("长文.pdf", "application/pdf", stream.getvalue(), Mock(), vision)
    vision.generate_images_json.assert_not_called()


"""后批失败测试函数：不能保存仅首批成功的PDF为完整识别结果。"""

def test_pdf_later_batch_failure_is_not_partial_success(monkeypatch):
    from app.llm.client import ModelClientError
    from app.services.attachment.analysis import analyze_pdf_images

    monkeypatch.setattr("app.services.attachment.analysis.render_pdf_pages", lambda content:
        [b"page"] * 5)
    vision = Mock()
    vision.generate_images_json.side_effect = [output(waypoints=[]), ModelClientError("读取失败")]
    with pytest.raises(AttachmentAnalysisError, match="第5至5页暂未读完"):
        analyze_pdf_images(b"pdf", vision)


"""缺视觉配置测试函数：需要回看页面时给用户自然提示。"""

def test_pdf_visual_fallback_needs_config(monkeypatch):
    from app.schemas.document.base import ParsedDocument, ParsedSection

    monkeypatch.setattr("app.services.attachment.analysis.parse_document", lambda *args:
        ParsedDocument(sections=[ParsedSection(text="杭州", order=1)], warnings=["第2页无文字"]))
    with pytest.raises(AttachmentAnalysisError, match="未读清楚.*百炼"):
        analyze_content("攻略.pdf", "application/pdf", b"pdf", Mock())


"""多图上传预算测试函数：超过批量数量或上传量时不发送任何请求。"""

@pytest.mark.parametrize("images", [
    [(b"page", "image/jpeg")] * 5,
    [(b"x" * (6 * 1024 * 1024), "image/jpeg")] * 2,
])
def test_qwen_rejects_oversized_batch_before_request(images):
    from app.llm.client import ModelClientError

    vision = QwenVisionClient.__new__(QwenVisionClient)
    vision.model = Mock()
    with pytest.raises(ModelClientError):
        vision.generate_images_json(images, "读取页面")
    vision.model.invoke.assert_not_called()


"""模糊必经测试函数：带问号的地名即使在图片引文中出现，也必须明确留待确认。"""

def test_pdf_unclear_name_is_explicit_not_silently_dropped(monkeypatch):
    from app.services.attachment.analysis import analyze_pdf_images

    monkeypatch.setattr("app.services.attachment.analysis.render_pdf_pages", lambda content:
        [b"page"])
    vision = Mock()
    vision.generate_images_json.return_value = output(waypoints=[{
        "name": "岳麓？院", "order": None, "evidence": "【第1页】岳麓？院",
        "needs_confirmation": True,
    }])
    result = analyze_pdf_images(b"pdf", vision)
    assert result.waypoints == []
    assert any(w.startswith("未确认地点：") and "岳麓？院" in w for w in result.warnings)


"""物理页标签测试函数：逐图标出真实页码，避免模型误用页面底部的印刷页码。"""

def test_qwen_labels_each_pdf_image():
    vision = QwenVisionClient.__new__(QwenVisionClient)
    vision.model, vision.timeout = Mock(), 60
    vision.endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vision.model.model_name = "qwen3-vl-plus"
    vision.model.invoke.return_value = AIMessage(
        content=output(), response_metadata={"finish_reason": "stop"},
    )
    vision.generate_images_json([(b"page", "image/jpeg")] * 2, "读取页面", first_page=5)
    content = vision.model.invoke.call_args.args[0][-1].content
    assert "实际第5页" in content[1]["text"]
    assert "image_url" in content[2]
    assert "实际第6页" in content[3]["text"]
    assert "image_url" in content[4]
    assert vision.model.invoke.call_args.kwargs["timeout"] == 120


"""大量待确认名称测试函数：按批合并提醒而不因项目数量截断任何不清楚的名称。"""

def test_pdf_groups_all_unclear_names_without_dropping(monkeypatch):
    from app.services.attachment.analysis import analyze_pdf_images

    monkeypatch.setattr("app.services.attachment.analysis.render_pdf_pages", lambda content:
        [b"page"])
    names = [f"地名{index}" for index in range(35)]
    vision = Mock()
    vision.generate_images_json.return_value = output(waypoints=[{
        "name": name, "order": None, "evidence": "【第1页】看不清", "needs_confirmation": True,
    } for name in names])
    result = analyze_pdf_images(b"pdf", vision)
    assert not result.waypoints and len(result.warnings) == 1
    assert all(name in result.warnings[0] for name in names)


"""城市后缀测试函数：不同批次的长沙和长沙市表示同一城市。"""

def test_pdf_city_suffix_does_not_create_false_multi_city_warning(monkeypatch):
    from app.services.attachment.analysis import analyze_pdf_images

    monkeypatch.setattr("app.services.attachment.analysis.render_pdf_pages", lambda content:
        [b"page"] * 5)
    vision = Mock()
    vision.generate_images_json.side_effect = [
        output(city="长沙", waypoints=[]), output(city="长沙市", waypoints=[]),
    ]
    assert analyze_pdf_images(b"pdf", vision).city == "长沙"


"""整份容量测试函数：多批明确地点可以超过单批100项，不能为适配旧容量丢掉后页。"""

def test_pdf_preserves_more_than_one_hundred_places(monkeypatch):
    from app.services.attachment.analysis import analyze_pdf_images

    monkeypatch.setattr("app.services.attachment.analysis.render_pdf_pages", lambda content:
        [b"page"] * 5)
    vision = Mock()
    vision.generate_images_json.side_effect = [output(waypoints=[{
        "name": f"地点{index}", "order": None, "evidence": f"【第{page}页】地点{index}",
        "needs_confirmation": True,
    } for index in range(start, end)]) for start, end, page in [(0, 80, 1), (80, 120, 5)]]
    assert len(analyze_pdf_images(b"pdf", vision).waypoints) == 120
