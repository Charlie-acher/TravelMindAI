"""验证本项目的配置契约，而不是调用真实模型。

pytest 会自动发现 test_ 开头的函数并执行，作用类似 JUnit 的 @Test。
每个测试只准备当前用例的数据，不依赖你电脑上的真实 .env。
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from travelmind.settings import load_settings


@pytest.fixture(autouse=True)
def clear_project_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试开始前清除项目变量，避免个人环境影响测试结果。

    monkeypatch 是 pytest 提供的临时修改工具：测试结束后会自动恢复。
    autouse=True 表示每个测试自动使用这个准备步骤，无需逐个手动调用。
    """
    monkeypatch.delenv("TRAVELMIND_APP_NAME", raising=False)
    monkeypatch.delenv("TRAVELMIND_ENVIRONMENT", raising=False)
    monkeypatch.delenv("TRAVELMIND_DATABASE_URL", raising=False)


def test_defaults_do_not_require_a_local_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """即使当前目录有错误的 .env，不传路径时也只采用默认值。

    这个测试能抓住“自动读取当前目录配置，导致换个目录就读错文件”的改动。
    tmp_path 是 pytest 创建的临时目录，不会写入你的真实配置。
    """
    (tmp_path / ".env").write_text("TRAVELMIND_ENVIRONMENT=wrong\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.app_name == "TravelMindAI"
    assert settings.environment == "development"


def test_database_is_optional_for_budget_only_mode() -> None:
    """预算计算不需要数据库；没有数据库地址时仍能加载配置。"""
    assert load_settings().database_url is None


def test_database_url_is_loaded_without_exposing_password(tmp_path: Path) -> None:
    """配置能读取连接地址，但打印配置对象时不能直接显示数据库密码。"""
    env_file = tmp_path / ".env"
    # 这里只用虚构密码，不读取开发电脑上的真实配置，也不会连接数据库。
    address = "postgresql+psycopg://reader:example_secret@127.0.0.1:5432/example"
    env_file.write_text(f"TRAVELMIND_DATABASE_URL={address}\n", encoding="utf-8")

    settings = load_settings(env_file)

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == address
    assert "example_secret" not in repr(settings)
    assert "example_secret" not in settings.model_dump_json()


def test_explicit_file_is_read_and_unrelated_settings_are_ignored(tmp_path: Path) -> None:
    """只有显式传入的文件被读取；旧实验的其他配置项不应让正式包启动失败。"""
    env_file = tmp_path / "example.env"
    env_file.write_text(
        "TRAVELMIND_APP_NAME=旅行测试\n"
        "TRAVELMIND_ENVIRONMENT=test\n"
        "UNRELATED_EXPERIMENT_SETTING=example\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file)

    assert settings.app_name == "旅行测试"
    assert settings.environment == "test"


def test_environment_variables_override_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """部署环境指定的值优先于文件，不能被开发用的 .env 覆盖。"""
    env_file = tmp_path / "example.env"
    env_file.write_text("TRAVELMIND_ENVIRONMENT=development\n", encoding="utf-8")
    monkeypatch.setenv("TRAVELMIND_ENVIRONMENT", "production")

    assert load_settings(env_file).environment == "production"


@pytest.mark.parametrize(
    ("variable", "value", "field"),
    [
        ("TRAVELMIND_ENVIRONMENT", "prodution", "environment"),
        ("TRAVELMIND_APP_NAME", "   ", "app_name"),
    ],
)
def test_invalid_configuration_reports_the_correct_field(
    variable: str, value: str, field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """参数化让同一测试跑两次，分别验证环境名拼错和名称为空。

    不能只检查“任意异常”：还要确认报错确实来自我们填写错误的那个字段。
    """
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError) as error:
        load_settings()

    assert error.value.errors()[0]["loc"] == (field,)
