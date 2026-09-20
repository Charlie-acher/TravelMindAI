"""
配置层：读取并校验应用、数据库、模型和资料保存目录的设置。
"""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类：集中保存各模块需要的配置，供应用启动和业务服务使用。"""

    # Field 给字段增加校验规则：名称至少一个字符，不能是空字符串。
    app_name: str = Field(default="TravelMindAI", min_length=1)

    # Literal 限定可选字符串，效果接近枚举；拼错时立即报告 ValidationError。
    environment: Literal["development", "test", "production"] = "development"

    # 数据库连接地址，对应环境变量 TRAVELMIND_DATABASE_URL。
    # 地址包含用户名和密码，因此使用 SecretStr：打印配置时会显示为 **********。
    # SecretStr 是显示脱敏，不是加密；数据库驱动连接时仍然需要取出真实值。
    # None 表示没有配置数据库，已有的纯预算接口仍可使用。
    database_url: SecretStr | None = None

    # 原文件统一保存在项目temp/uploads；属于业务资料，阶段清理时必须保留。
    # 可通过环境变量改为其他绝对路径；这里仅记录路径，不创建目录。
    document_upload_dir: Path = Path(__file__).resolve().parents[2] / "temp" / "uploads"

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

    # 私人图片只发送到显式配置的百炼视觉入口，不自动复用文本或向量密钥。
    vision_api_key: SecretStr | None = None
    vision_base_url: HttpUrl | None = None
    vision_model: str = Field(default="qwen3-vl-plus", min_length=1)
    vision_timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)

    # 本机MinerU服务；留空保留旧附件流程，正式部署配置为127.0.0.1:8010。
    mineru_base_url: HttpUrl | None = None
    mineru_timeout_seconds: float = Field(default=600, gt=0, le=720, allow_inf_nan=False)

    # 向量服务独立配置，不借用聊天模型的地址、名称或密钥。
    # 暂未开通时留空，已有Web功能仍可启动；试跑命令会检查是否配齐。
    embedding_provider: str | None = Field(default=None, min_length=1)
    embedding_base_url: HttpUrl | None = None  # API根地址，例如https://服务域名/v1。
    embedding_model: str | None = Field(default=None, min_length=1)
    embedding_api_key: SecretStr | None = None
    # 维度是每段文字返回的数字个数；这里只核对模型原生维度，不要求服务降维。
    embedding_dimensions: int | None = Field(default=None, ge=1, le=65536)
    embedding_version: str | None = Field(default=None, min_length=1)  # 本项目的索引版本标签。
    embedding_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)

    # Milvus只在明确配置后启用；本地Docker端口仅绑定127.0.0.1。
    milvus_url: HttpUrl | None = None
    milvus_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)

    # 地图统一使用百度官方MCP；密钥仅由后端读取，留空时地图工具不可用。
    baidu_map_api_key: SecretStr | None = Field(default=None, validation_alias="BAIDU_MAP_API_KEY")

    # 联网搜索独立于DeepSeek；没有搜索Key时仍可使用知识库，页面标明尚未补查。
    tavily_api_key: SecretStr | None = None
    # 首版只检索政府公开信息；可显式添加已核实的景区官网域名，不自动信任任意网站。
    travel_web_domains: list[str] = Field(default_factory=lambda: ["gov.cn"])

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
