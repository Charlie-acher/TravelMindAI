"""数据库连接入口的测试：默认不访问 PostgreSQL，也不需要个人密码。

这里先检查配置错误、导入和创建引擎时的行为；真实连接另用命令行入口验收。
如果有人把网络连接提前到 create_database_engine()，下面的测试就会失败。
"""

import socket
from pathlib import Path

import pytest
from pydantic import SecretStr

from travelmind.settings import Settings


def test_missing_database_address_has_a_clear_error() -> None:
    """缺配置时给出明确说明，不能静默连接到某个默认数据库。"""
    from travelmind.persistence.database import create_database_engine

    with pytest.raises(ValueError, match="TRAVELMIND_DATABASE_URL"):
        create_database_engine(Settings(database_url=None))


@pytest.mark.parametrize(
    "address",
    [
        "",
        "not-a-url-example_secret",
        "sqlite:///example_secret.db",
        "postgresql://reader:example_secret@localhost/example",
    ],
)
def test_invalid_database_address_never_echoes_credentials(address: str) -> None:
    """本阶段只支持明确选择 psycopg 驱动的 PostgreSQL URL。

    错误信息不应原样打印包含密码的地址；尤其需要覆盖 URL 解析失败的情况。
    """
    from travelmind.persistence.database import create_database_engine

    with pytest.raises(ValueError) as error:
        create_database_engine(Settings(database_url=SecretStr(address)))

    assert "example_secret" not in str(error.value)


def test_creating_engine_does_not_open_a_network_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine 类似连接池管理器，真正调用 connect() 时才应连接。

    这里拦截网络连接，因此即使电脑上没有 PostgreSQL 也能验证这个契约。
    """
    from travelmind.persistence.database import create_database_engine

    def reject_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("创建引擎时不应该连接网络")

    monkeypatch.setattr(socket.socket, "connect", reject_connection)
    settings = Settings(
        database_url=SecretStr("postgresql+psycopg://reader:example_secret@localhost/example")
    )
    engine = create_database_engine(settings)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        assert "example_secret" not in repr(engine)
    finally:
        # 即使断言失败也清理连接池管理器，这是 finally 的用途。
        engine.dispose()


def test_cli_missing_configuration_returns_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """命令行检查失败应返回非零退出码，并提示用户检查配置。"""
    from travelmind.persistence.database import main

    monkeypatch.delenv("TRAVELMIND_DATABASE_URL", raising=False)
    assert main([]) == 1
    output = capsys.readouterr()
    assert "TRAVELMIND_DATABASE_URL" in output.err
    assert "Traceback" not in output.err


def test_cli_rejects_a_missing_explicit_config_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """文件路径拼错时直接指出文件不存在，不能悄悄忽略 --env-file。"""
    from travelmind.persistence.database import main

    assert main(["--env-file", str(tmp_path / "missing.env")]) == 1
    assert "配置文件不存在" in capsys.readouterr().err


def test_cli_reports_connection_failure_without_printing_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """模拟驱动认证失败，只验证命令行的错误脱敏，不把模拟结果当成连库成功。"""
    from sqlalchemy.exc import OperationalError

    from travelmind.persistence import database

    monkeypatch.delenv("TRAVELMIND_DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TRAVELMIND_DATABASE_URL=postgresql+psycopg://u:example_secret@localhost/db\n",
        encoding="utf-8",
    )

    def reject_check(*args: object, **kwargs: object) -> dict[str, str]:
        raise OperationalError("SELECT", {}, Exception("example_secret"))

    monkeypatch.setattr(database, "check_connection", reject_check)
    assert database.main(["--env-file", str(env_file)]) == 1
    output = capsys.readouterr()
    assert "连接失败" in output.err
    assert "example_secret" not in output.err + output.out
