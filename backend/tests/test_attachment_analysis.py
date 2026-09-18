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
