"""
配置层：读取并校验应用、数据库和模型配置。
"""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类：保存应用名称、运行环境、数据库和模型设置。"""

    # Field 给字段增加校验规则：名称至少一个字符，不能是空字符串。
    app_name: str = Field(default="TravelMindAI", min_length=1)

    # Literal 限定可选字符串，效果接近枚举；拼错时立即报告 ValidationError。
    environment: Literal["development", "test", "production"] = "development"

    # 数据库连接地址，对应环境变量 TRAVELMIND_DATABASE_URL。
    # 地址包含用户名和密码，因此使用 SecretStr：打印配置时会显示为 **********。
    # SecretStr 是显示脱敏，不是加密；数据库驱动连接时仍然需要取出真实值。
    # None 表示没有配置数据库，已有的纯预算接口仍可使用。
    database_url: SecretStr | None = None

    # 单模型抽取配置；未提供密钥时，原来的预算、数据库服务仍能启动。
    # 优先使用正式变量名，同时兼容学习示例里的DS_API_KEY。
    # 兼容变量名不会自动读取根目录.env，仍须显式传env_file路径。
    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("TRAVELMIND_DEEPSEEK_API_KEY", "DS_API_KEY"),
    )
    # 真实模型名是配置项，沿用已有学习示例；不在import时探测提供方。
    deepseek_model: str = Field(default="deepseek-v4-pro", min_length=1)
    # HTTP连接、读、写和连接池等待均使用此超时秒数，不能为0或无穷大。
    deepseek_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)

    # 环境变量和配置文件的读取规则。
    model_config = SettingsConfigDict(
        env_prefix="TRAVELMIND_",  # app_name 对应 TRAVELMIND_APP_NAME。
        env_file_encoding="utf-8",  # 保证 .env 中的中文名称能够正确读取。
        extra="ignore",  # 已有 .env 中其他实验的变量不属于本阶段，忽略即可。
        str_strip_whitespace=True,  # 先去掉两端空格，使纯空格名称也无法通过校验。
        populate_by_name=True,  # 设置了环境变量别名后，仍允许用字段名创建测试配置。
    )


"""配置加载函数：读取环境变量和指定配置文件，返回已校验的配置。"""

def load_settings(env_file: Path | None = None) -> Settings:
    # 只读取明确指定的文件；未传路径时不会自动寻找 .env。
    return Settings(_env_file=env_file)
