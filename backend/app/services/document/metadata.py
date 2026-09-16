"""资料标签业务层：从文件名和明确的正文标注预填标签，不调用模型或地图。"""

import re
from pathlib import Path

from app.schemas.document.base import DocumentMetadata, ParsedSection

# ponytail: 文件名使用常见城市词表；词表外通过“城市：名称”识别，无法确定就留空。
CITIES = (
    "北京 天津 上海 重庆 石家庄 太原 呼和浩特 沈阳 大连 长春 哈尔滨 南京 苏州 无锡 "
    "常州 扬州 镇江 南通 徐州 杭州 宁波 温州 绍兴 嘉兴 湖州 金华 台州 舟山 丽水 "
    "衢州 合肥 黄山 芜湖 福州 厦门 泉州 漳州 南昌 九江 景德镇 济南 青岛 烟台 威海 "
    "郑州 洛阳 开封 武汉 宜昌 长沙 张家界 广州 深圳 珠海 佛山 东莞 惠州 汕头 "
    "南宁 桂林 北海 海口 三亚 成都 乐山 绵阳 贵阳 遵义 昆明 大理 丽江 西双版纳 "
    "拉萨 西安 咸阳 兰州 敦煌 西宁 银川 乌鲁木齐 喀什 香港 澳门 台北 台南 高雄"
).split()


"""标签识别函数：只从文件名和明确标注提取城市及三类业务类别。"""

def infer_metadata(file_name: str, sections: list[ParsedSection]) -> DocumentMetadata:
    stem = Path(file_name).stem
    cities = {city for city in CITIES if city in stem}
    categories = {category for category in ("住宿", "景点", "餐馆") if category in stem}
    # 正文只认独立标注行，不把“出发城市”或普通段落里的城市当作资料归属。
    for section in sections:
        for line in section.text.splitlines():
            line = line.strip().lstrip("#-* ").replace("**", "")
            match = re.fullmatch(r"(?:城市|city)\s*[:：]\s*([\u4e00-\u9fff]{2,12})", line, re.I)
            if match:
                cities.add(match[1].removesuffix("市"))
            match = re.fullmatch(r"(?:类别|category)\s*[:：]\s*(住宿|景点|餐馆)", line, re.I)
            if match:
                categories.add(match[1])
    return DocumentMetadata(
        city=next(iter(cities)) if len(cities) == 1 else None,
        category=next(iter(categories)) if len(categories) == 1 else None,
    )
