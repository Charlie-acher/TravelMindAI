"""聊天业务层：识别附近餐饮追问，沿用已核实的景点锚点并调用高德。"""

import re

from app.schemas.dining import DiningResult
from app.schemas.document.answer import MapLookup
from app.schemas.requirement.history import SavedRequirementTurn
from app.services.amap import AmapClient
from app.services.chat.events import progress

PREFERENCES = ("烧烤", "火锅", "家常菜", "家常", "川菜", "粤菜", "杭帮菜", "面馆", "小吃")


"""餐饮处理函数：餐厅事实全部由地图返回，缺少明确地点时只追问，不猜位置。"""


def handle_dining(message: str, destination: str | None, turns: list[SavedRequirementTurn],
                  maps: AmapClient) -> DiningResult | None:
    previous = getattr(turns[-1].response, "dining", None) if turns else None
    preference = next((word for word in PREFERENCES if word in message), None)
    if preference == "家常":
        preference = "家常菜"
    if previous is not None and re.fullmatch(r"(?:都行|都可以|不限|随便|没要求)[。！!]?", message):
        preference = "不限"
    if not preference and not re.search(
        r"饭店|餐厅|餐馆|餐饮|吃饭|吃点|美食|哪里吃|吃什么|好吃|吃的", message,
    ):
        return None
    candidates: list[MapLookup] = []
    for saved in reversed(turns):
        dining = getattr(saved.response, "dining", None)
        if dining is not None and dining.anchor is not None:
            candidates = [dining.anchor]
            break
        knowledge = saved.response.knowledge
        if knowledge is not None and knowledge.attractions:
            candidates = [card.location for card in knowledge.attractions]
            break
    # 目的地切换后不能继续用旧城市的中心点；没有新城市时可以沿用已验证上下文。
    candidates = [item for item in candidates if not destination
                  or item.city.removesuffix("市") == destination.removesuffix("市")]
    named = [item for item in candidates if item.name in message
             or item.matched_name and item.matched_name in message]
    explicit = re.search(r"(.{2,80}?)(?:周边|周围|附近|旁边)", message)
    requested = None
    if explicit:
        requested = re.sub(r"^(?:(?:我想|我要|帮我|请|推荐|找找|找|看看|想在|在|去|问一下)\s*)+",
                           "", explicit[1]).strip(" ，,的")
        if requested in ("这", "那", "这里", "那里", "它", "这个景点", "那个景点", "景点", "这儿"):
            requested = None
    if requested:
        if destination and requested.startswith(destination):
            requested = requested[len(destination):].lstrip("市的 ")
        named = [item for item in candidates if requested in (item.name, item.matched_name)]
    anchor = named[0] if len(named) == 1 else None
    ordinal = re.search(r"第([一二三123])个", message)
    if anchor is None and ordinal:
        index = {"一": 0, "二": 1, "三": 2, "1": 0, "2": 1, "3": 2}[ordinal[1]]
        anchor = candidates[index] if index < len(candidates) else None
        requested = None
    if (destination and destination.removesuffix("市") in message
            and not requested and not named and not ordinal):
        # 用户重新泛问整座城市时，不把旧景点偷偷变成本轮的检索范围。
        candidates = []
    if re.search(r"(?:不吃|不要|不想吃|不喜欢吃?|不考虑)\s*(?:" + "|".join(PREFERENCES) + ")",
                 message):
        return DiningResult(
            status="needs_clarification",
            anchor=anchor or (candidates[0] if len(candidates) == 1 and not requested else None),
            clarification="明白，你还有什么想吃的？可以告诉我其他口味，例如家常菜。",
        )
    if requested and anchor is None:
        if not destination:
            return DiningResult(status="needs_clarification", preference=preference,
                                clarification=f"你说的“{requested}”在哪个城市？")
        progress("map", "正在用高德确认你说的地点")
        anchor = maps.lookup(destination, requested)
    if anchor is None and not requested and len(candidates) == 1 and not ordinal:
        anchor = candidates[0]
    if anchor is None or anchor.status != "found" or anchor.location is None:
        status = anchor.status if anchor is not None else None
        if status in ("error", "unconfigured"):
            return DiningResult(status=status, anchor=anchor, preference=preference)
        return DiningResult(status="needs_clarification", anchor=anchor, preference=preference,
                            clarification="你想找哪个城市、哪个景点附近的饭店？请给出具体地点名称。")
    progress("dining", "正在查询附近餐厅，核对高德评分")
    result = maps.nearby_dining(anchor, preference)
    if preference is None:
        result.clarification = "你更想吃烧烤、火锅，还是家常菜？也可以说不限。"
    return result


"""餐饮回复函数：用真实状态生成说明，明确半径和评分口径。"""


def dining_reply(result: DiningResult) -> str:
    if result.status == "needs_clarification":
        return result.clarification or "请告诉我具体城市和景点。"
    if result.status == "unconfigured":
        return "高德餐饮查询尚未配置，暂时无法核实附近餐厅和评分。"
    if result.status == "error":
        return "高德查询暂时失败，暂时无法核实附近餐厅和评分，请稍后再试。"
    name = result.anchor.matched_name or result.anchor.name if result.anchor else "该地点"
    distance = (f"{result.radius_m / 1000:g}公里" if result.radius_m >= 1000
                else f"{result.radius_m}米")
    scope = f"{name}周边{distance}内（直线范围）"
    if result.status == "found":
        reply = (f"可以先看看这{len(result.items)}家，都是高德本次查询里4分及以上的店，"
                 f"范围在{scope}。")
    elif result.status == "ratings_unavailable":
        reply = f"查了{scope}，但这次高德返回的店没有有效评分，暂时没法按4分及以上帮你挑。"
        reply += "可以换个口味，或换个地点附近再找找。"
    else:
        reply = f"这次在{scope}，还没找到符合你偏好且高德评分4分及以上的店。"
        reply += "可以换个口味，或换个地点附近再找找。"
    if result.clarification:
        reply += result.clarification
    return reply
