"""验收命令层：检查百度MCP工具发现与真实天气查询，不输出密钥或保存业务数据。"""

import argparse
from pathlib import Path

from app.config import load_settings
from app.services.baidu import BaiduMaps

"""连通性检查函数：只读查询返回明确状态，失败以非零退出码结束。"""


def main() -> int:
    parser = argparse.ArgumentParser(description="验证百度地图MCP")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--district", default="330100", help="天气查询行政区代码，默认杭州")
    args = parser.parse_args()
    with BaiduMaps(load_settings(args.env_file)) as maps:
        print("连接状态：", maps.tools.status)
        print("开放工具：", ", ".join(tool.name for tool in maps.tools.tools))
        if maps.tools.status != "ready":
            return 1
        try:
            body = maps.query("map_weather", district_id=args.district)
        except Exception:
            print("天气查询失败；请核对服务端AK的权限、IP白名单和配额。")
            return 1
        result = body.get("result", {})
        print("天气查询成功：", result.get("location"), result.get("now"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
