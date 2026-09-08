"""集中读取并校验应用配置，类似 Java 的 @ConfigurationProperties。

管理应用名称、运行环境和可选的 PostgreSQL 连接地址。
本文件定义类和函数，但不在模块顶层调用它们；import 不会读取 .env。
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """配置对象：继承 BaseSettings，获得环境变量读取和数据校验能力。

    冒号后是类型提示，等号后是默认值；这与 Java 的字段声明形式不同。
    Pydantic 在创建对象时实际校验值，普通 Python 类型提示本身不会校验。
    """

    # Field 给字段增加校验规则：名称至少一个字符，不能是空字符串。
    app_name: str = Field(default="TravelMindAI", min_length=1)

    # Literal 限定可选字符串，效果接近枚举；拼错时立即报告 ValidationError。
    environment: Literal["development", "test", "production"] = "development"

    # 数据库连接地址，对应环境变量 TRAVELMIND_DATABASE_URL。
    # 地址包含用户名和密码，因此使用 SecretStr：打印配置时会显示为 **********。
    # SecretStr 是显示脱敏，不是加密；数据库驱动连接时仍然需要取出真实值。
    # None 表示没有配置数据库，已有的纯预算接口仍可使用。
    database_url: SecretStr | None = None

    # model_config 是 Pydantic 的配置入口，不是我们发明的新业务字段。
    model_config = SettingsConfigDict(
        env_prefix="TRAVELMIND_",  # app_name 对应 TRAVELMIND_APP_NAME。
        env_file_encoding="utf-8",  # 保证 .env 中的中文名称能够正确读取。
        extra="ignore",  # 已有 .env 中其他实验的变量不属于本阶段，忽略即可。
        str_strip_whitespace=True,  # 先去掉两端空格，使纯空格名称也无法通过校验。
    )


def load_settings(env_file: Path | None = None) -> Settings:
    """需要配置时才调用；默认不自动寻找 .env。

    参数 env_file：可传入 pathlib.Path 文件路径；None 表示不读文件。
    返回值 Settings：已经通过校验的配置对象；非法值会抛出 ValidationError。

    示例：
        settings = load_settings()  # 只读操作系统环境变量，缺少时使用默认值。
        settings = load_settings(Path("E:/TravelMindAI/.env"))  # 显式读取指定文件。

    优先级由 BaseSettings 负责：环境变量 > 指定文件 > 字段默认值。
    使用显式路径是为了避免启动目录变化时意外读到另一个项目的 .env。
    """
    # _env_file 是 BaseSettings 提供的构造参数，不是需要手工解析的文件内容。
    # 每次调用都新建对象，不缓存，方便测试和后续由应用启动过程管理生命周期。
    return Settings(_env_file=env_file)
