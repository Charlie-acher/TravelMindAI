"""验证命令行入口的参数、退出码和配置错误；不向真实模型发请求。"""

import json
from pathlib import Path

import pytest

from scripts import demo_requirement

"""清理个人模型环境变量，保证无密钥测试在任何开发者电脑上含义一致。"""

@pytest.fixture(autouse=True)
def clear_model_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TRAVELMIND_DEEPSEEK_API_KEY",
        "DS_API_KEY",
        "TRAVELMIND_DEEPSEEK_MODEL",
        "TRAVELMIND_DEEPSEEK_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


"""忘记配置密钥时友好报错，不输出Python堆栈。"""

def test_missing_key_has_readable_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert demo_requirement.main(["--message", "想去杭州"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "未配置" in captured.err
    assert "Traceback" not in captured.err


"""显式指定了不存在的配置文件时不能悄悄退回其他配置。"""

def test_missing_file_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        demo_requirement.main(
            [
                "--message",
                "去杭州",
                "--env-file",
                str(tmp_path / "absent.env"),
            ]
        )
        == 1
    )
    assert "不存在" in capsys.readouterr().err


"""非法配置不会回显原值；Pydantic默认异常文本可能携带input，因此需单独处理。"""

def test_bad_configuration_does_not_echo_value(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TRAVELMIND_DEEPSEEK_TIMEOUT_SECONDS", "private-invalid-setting")
    assert demo_requirement.main(["--message", "去杭州"]) == 1
    error = capsys.readouterr().err
    assert "配置" in error
    assert "private-invalid-setting" not in error


"""命令能串起真实服务、JSON校验和追问，只有模型的网络调用被替换。"""

def test_cli_prints_structured_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answer = {
        "intent": "plan_trip",
        "destination": "杭州",
        "origin": None,
        "start_date": None,
        "end_date": None,
        "days": None,
        "travelers": None,
        "total_budget": None,
        "pace": None,
        "interests": [],
        "dietary": [],
        "lodging_preferences": [],
        "hard_constraints": [],
        "excluded_items": [],
        "assumptions": [],
    }
    # 真实客户端照常构造，但generate_json不再联网，直接返回这份预设答案。
    monkeypatch.setenv("TRAVELMIND_DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setattr(
        demo_requirement.DeepSeekClient,
        "generate_json",
        lambda self, messages: json.dumps(answer),
    )
    assert (
        demo_requirement.main(
            [
                "--message",
                "想去杭州",
                "--reference-date",
                "2026-09-09",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["original_message"] == "想去杭州"
    assert result["reference_date"] == "2026-09-09"
    assert result["extraction"]["destination"] == "杭州"
    assert result["missing_required_fields"] == ["days", "travelers", "total_budget"]
