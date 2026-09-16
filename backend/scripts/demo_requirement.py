"""把一句自然语言转换成旅行需求的独立入口，不启动Web服务、不写数据库。

在backend目录执行（会发送真实DeepSeek请求并产生API费用）：
    python -X utf8 -m scripts.demo_requirement --env-file .env --message "想去杭州"
若复用根目录学习配置，显式使用 --env-file ../.env，支持其中已有的DS_API_KEY。
"""

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from pydantic import ValidationError

from app.config import load_settings
from app.llm.client import DeepSeekClient, ModelClientError
from app.services.requirement.extract import RequirementExtractionError, extract_requirements

"""解析参数并执行一次抽取；成功返回0，配置或抽取失败返回1。

默认参考日期取UTC+8日历日期，避免Windows本机时区设置影响“明天”的含义。
允许通过--reference-date固定日期，便于复现同一句相对日期需求。
"""

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用DeepSeek将自然语言提取成结构化旅行需求")
    parser.add_argument("--message", required=True, help="本次要提取的用户原话")
    parser.add_argument("--env-file", type=Path, help="显式指定配置文件，不自动寻找.env")
    parser.add_argument(
        "--reference-date",
        type=date.fromisoformat,
        default=datetime.now(timezone(timedelta(hours=8))).date(),
        help="相对日期的计算基准YYYY-MM-DD，默认上海今天",
    )
    arguments = parser.parse_args(argv)
    if arguments.env_file is not None and not arguments.env_file.is_file():
        print("配置文件不存在，请检查--env-file路径。", file=sys.stderr)
        return 1
    try:
        settings = load_settings(arguments.env_file)
        # with保证成功和异常两条路径都会关闭HTTP连接，避免遗留连接池。
        with httpx.Client() as http:
            model = DeepSeekClient(settings, http)
            result = extract_requirements(
                arguments.message,
                model,
                reference_date=arguments.reference_date,
            )
    except ValidationError:
        print("模型配置不合法，请检查模型名、密钥及超时设置。", file=sys.stderr)
        return 1
    except (ModelClientError, RequirementExtractionError, ValueError) as error:
        # 这些异常的消息由我们控制；不输出提供方响应或Pydantic的原始input。
        print(str(error), file=sys.stderr)
        return 1
    print(result.model_dump_json(indent=2, ensure_ascii=False))
    return 0


# 只有直接运行本模块才执行main；被测试或应用import时不会读配置、联网或打印。
if __name__ == "__main__":
    raise SystemExit(main())
