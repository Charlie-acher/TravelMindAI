"""交通业务层：读取12306公开查询并检索航司官方资料，失败保留具体缺口。"""

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from app.schemas.transport import (
    RailOption,
    RailResult,
    TransportLeg,
    TransportQuery,
    TransportResult,
)
from app.services.chat.events import check_cancelled, progress
from app.services.usage import usage_call
from app.services.web_search import WebSearchClient

RAIL_ROOT = "https://kyfw.12306.cn"
AIRLINE_DOMAINS = ["airchina.com.cn", "ceair.com", "csair.com", "hnair.com", "xiamenair.com",
                   "shenzhenair.com", "sichuanair.com", "shandongair.com.cn", "ch.com",
                   "juneyaoair.com"]
CHINA = timezone(timedelta(hours=8))


"""条件合并函数：仅在理解层明确接续时调用，未提及保留，显式解除单独处理。"""

def merge_transport(query: TransportQuery, previous: TransportQuery,
                    clear_fields: list[str]) -> TransportQuery:
    values = previous.model_dump()
    values.update({key: value for key, value in query.model_dump(
        exclude_unset=True, exclude_none=True).items() if value != ""})
    for field in clear_fields:
        values[field] = "" if field == "preferences" else None
    return TransportQuery.model_validate(values)


class RailClient:
    """铁路客户端类：同轮复用站名和查询入口，不使用登录凭据，不绕过验证码。"""

    """初始化函数：借用HTTP连接，只在实际查询时加载官方页面。"""

    def __init__(self, http: httpx.Client) -> None:
        self.http = http
        self.stations: dict[str, str] = {}
        self.query_path = ""

    """读取函数：记录公开网站请求，停止信号在每次网络请求前后检查。"""

    def get(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        check_cancelled()
        with usage_call("12306", "public-query", RAIL_ROOT, "transport") as call:
            response = self.http.get(RAIL_ROOT + path, params=params, timeout=8)
            response.raise_for_status()
            call["units"] = 1
        check_cancelled()
        return response

    """入口加载函数：从官方页面读取当前路径，仅允许固定12306站内查询和站名资源。"""

    def prepare(self) -> None:
        if self.stations:
            return
        page = self.get("/otn/leftTicket/init").text
        query = re.search(r"CLeftTicketUrl\s*=\s*['\"](leftTicket/query[A-Za-z]*)['\"]", page)
        station = re.search(
            r"['\"](/otn/resources/js/framework/station_name\.js(?:\?[^'\"]*)?)['\"]", page)
        if not query or not station:
            raise ValueError("官方入口未能读取")
        raw = self.get(station[1]).text
        for record in raw.split("@"):
            parts = record.split("|")
            if len(parts) >= 4 and re.fullmatch(r"[A-Z]{3}", parts[2]):
                self.stations[parts[1]] = parts[2]
        if not self.stations:
            raise ValueError("站名未能读取")
        self.query_path = "/otn/" + query[1]

    """单程查询函数：筛选直达高铁动车，展示最多三班；票价失败不丢失已取得的班次。"""

    def query(self, origin: str, destination: str, day: date, query: TransportQuery,
              today: date) -> RailResult:
        result = RailResult(status="unavailable")
        offset = (day - today).days
        if offset < 0 or offset >= 15:
            result.status = "past_date" if offset < 0 else "not_on_sale"
            result.note = "日期已过去，请确认出发日期。" if offset < 0 else (
                "按12306公布的15天（含当天）预售期，尚未进入查询范围；未开售不等于无车。")
            return result
        try:
            self.prepare()
            start = self.stations.get(origin.removesuffix("站").removesuffix("市"))
            end = self.stations.get(destination.removesuffix("站").removesuffix("市"))
            if not start or not end:
                result.status = "unknown_station"
                result.note = "未匹配到出发或到达车站，请补充车站名称。"
                return result
            result.source_url += "?" + urlencode({"date": day.isoformat(),
                "fs": f"{origin},{start}", "ts": f"{destination},{end}", "linktypeid": "dc"})
            body = self.get(self.query_path, {"leftTicketDTO.train_date": day.isoformat(),
                "leftTicketDTO.from_station": start, "leftTicketDTO.to_station": end,
                "purpose_codes": "ADULT"}).json()
            if body.get("status") is not True or not isinstance(body.get("data"), dict):
                raise ValueError("官方查询未成功")
            rows, names = body["data"]["result"], body["data"]["map"]
            if not isinstance(rows, list) or not isinstance(names, dict):
                raise ValueError("官方数据结构变化")
            candidates = []
            for row in rows:
                cells = row.split("|")
                if len(cells) < 36:
                    raise ValueError("官方车次字段变化")
                if not re.fullmatch(r"[GDC]\d+", cells[3]):
                    continue
                departure = datetime.fromisoformat(f"{day}T{cells[8]}").replace(tzinfo=CHINA)
                hours, minutes = map(int, cells[10].split(":"))
                duration = hours * 60 + minutes
                arrival = departure + timedelta(minutes=duration)
                if arrival.strftime("%H:%M") != cells[9]:
                    raise ValueError("官方时间字段不一致")
                if query.earliest_departure and departure.time() < query.earliest_departure:
                    continue
                arrival_day = (query.departure_date if query.previous_day_earliest_departure
                               and query.departure_date and day < query.departure_date else day)
                if query.latest_arrival and arrival > datetime.combine(
                        arrival_day, query.latest_arrival, tzinfo=CHINA):
                    continue
                option = RailOption(code=cells[3], from_station=names[cells[6]],
                    to_station=names[cells[7]], departure=departure, arrival=arrival,
                    duration_minutes=duration, seats=cells[30] or "未提供")
                # 未开放预订和无二等座均如实显示，不能据此承诺同行人都有座。
                if cells[11] != "Y":
                    option.seats = "暂不可预订"
                candidates.append((option, cells))
            candidates.sort(key=lambda item: item[0].departure)
            for option, cells in candidates[:3]:
                try:
                    price = self.get("/otn/leftTicket/queryTicketPrice", {
                        "train_no": cells[2], "from_station_no": cells[16],
                        "to_station_no": cells[17], "seat_types": cells[35],
                        "train_date": day.isoformat()}).json()
                    amount = ((price.get("data") or {}).get("O")
                              if price.get("status") is True else None)
                    if isinstance(amount, str) and re.fullmatch(r"[¥￥]\d+(?:\.\d{1,2})?", amount):
                        option.price = Decimal(amount[1:])
                except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                    pass
                result.options.append(option)
            result.status = "found" if result.options else "empty"
            result.note = ("最多展示三条直达高铁/动车候选，同车不同站分列，按出发时间排序；"
                           "未查询中转，余票和价格随时变化。")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            result.note = ("12306公开查询暂未成功，可能遇到网络或访问限制；"
                           "未取得结果不表示无车或售罄。")
        return result


"""交通汇总函数：往返分别查询铁路和航司；只补交通必需条件，不要求预算或景点。"""

def query_transport(query: TransportQuery, web: WebSearchClient | None, rail: RailClient,
                    *, today: date | None = None) -> TransportResult:
    result = TransportResult(query=query)
    result.missing = [label for key, label in (("origin", "出发城市"),
        ("destination", "到达城市"), ("departure_date", "出发日期")) if not getattr(query, key)]
    if result.missing:
        return result
    assert query.origin and query.destination and query.departure_date
    if query.return_date and query.return_date < query.departure_date:
        result.missing = ["返程日期早于去程，请确认日期"]
        return result
    directions = [(query.origin, query.destination, query.departure_date, query)]
    if query.previous_day_earliest_departure and "rail" in query.modes:
        # 增加前夜候选；即便为空或查询失败，原出发日也独立查询。
        directions.insert(0, (query.origin, query.destination,
            query.departure_date - timedelta(days=1), query.model_copy(update={
                "earliest_departure": query.previous_day_earliest_departure, "modes": ["rail"]})))
    if query.return_date:
        directions.append((query.destination, query.origin, query.return_date,
            query.model_copy(update={
            "earliest_departure": query.return_earliest_departure,
            "latest_arrival": query.return_latest_arrival})))
    for origin, destination, day, limits in directions:
        leg = TransportLeg(origin=origin, destination=destination, date=day)
        if "rail" in limits.modes:
            progress("transport", f"正在查询{day} {origin}至{destination}的12306车次和票价")
            leg.rail = rail.query(origin, destination, day, limits,
                                  today or datetime.now(CHINA).date())
        if "flight" in limits.modes:
            progress("transport", f"正在查询{day} {origin}至{destination}的航空公司公开信息")
            if web:
                leg.flights = web.search(f"{day} {origin} {destination} 航班 起飞 到达 票价",
                                         domains=AIRLINE_DOMAINS)
                check_cancelled()
            else:
                leg.flights.status = "unconfigured"
        result.legs.append(leg)
    return result


"""铁路展示函数：直接使用官方字段形成表格，金额运算不交给模型。"""

def render_rail(result: TransportResult) -> str:
    lines = []
    for index, leg in enumerate(result.legs):
        if not leg.rail:
            continue
        rail = leg.rail
        lines += [f"### {leg.date} {leg.origin} → {leg.destination} · 铁路查询", ""]
        previous_day = bool(result.query.departure_date and leg.date < result.query.departure_date)
        outbound = index == 0 or previous_day or (
            bool(result.query.previous_day_earliest_departure) and index == 1)
        earliest = (result.query.previous_day_earliest_departure if previous_day else
                    result.query.earliest_departure if outbound else
                    result.query.return_earliest_departure)
        latest = result.query.latest_arrival if outbound else result.query.return_latest_arrival
        if result.query.previous_day_earliest_departure and outbound:
            lines += ["额外考虑的前一晚。" if previous_day else "原出发日，继续查询。", ""]
        if earliest or latest:
            limits = ([f"{earliest:%H:%M}及以后出发"] if earliest else [])
            arrival_day = str(result.query.departure_date) if previous_day else "当日"
            limits += [f"{arrival_day}{latest:%H:%M}及以前到达"] if latest else []
            lines += ["筛选条件：" + "，".join(limits) + "。", ""]
        if rail.options:
            lines += ["| 车次 | 出发站 → 到达站 | 出发 → 到达 | 耗时 | 二等座/人 | "
                      "成人票参考小计 | 余票 |",
                      "| --- | --- | --- | --- | --- | --- | --- |"]
            for option in rail.options:
                price = f"¥{option.price}/人" if option.price is not None else "票价未取得"
                subtotal = (f"{result.query.travelers}人 ¥{option.price * result.query.travelers}"
                            if option.price is not None and result.query.travelers else "待确认")
                arrival = option.arrival.strftime("%H:%M")
                if option.arrival.date() != leg.date:
                    arrival += f"（{option.arrival:%m-%d}）"
                seats = option.seats
                if (result.query.travelers and seats.isdigit()
                        and int(seats) < result.query.travelers):
                    seats += "（不足同行人数）"
                lines.append(f"| **{option.code}** | {option.from_station} → {option.to_station} | "
                    f"{option.departure:%H:%M}—{arrival} | {option.duration_minutes // 60}小时"
                    f"{option.duration_minutes % 60}分 | {price} | {subtotal} | {seats} |")
        elif rail.status == "empty":
            lines += ["本次查询未返回符合时间条件的直达高铁/动车。"]
        lines += ["", rail.note, "", f"[12306查询来源]({rail.source_url}) · "
                  f"查询于 {rail.checked_at.astimezone(CHINA):%Y-%m-%d %H:%M}（北京时间）", ""]
    if lines:
        lines += ["成人票参考小计不含接驳，不代表儿童/优惠票价或已锁定多人余票；"
                  "出行前以12306为准。"]
    return "\n".join(lines)


"""航司展示函数：保留实际检索状态和官方链接，找到网页不等于取得当日库存。"""

def render_flights(result: TransportResult) -> str:
    labels = {"empty": "未查到该路线日期的公开资料", "error": "官方资料查询失败",
              "unconfigured": "官方资料检索未配置", "found": "已取得航司公开资料"}
    lines = []
    for leg in result.legs:
        if leg.flights.status == "not_requested":
            continue
        lines += [f"### {leg.date} {leg.origin} → {leg.destination} · 航司查询", "",
                  labels[leg.flights.status] + "；尚未取得当日可购买报价，暂不能比较准确差价。",
                  "以下为检索返回的官网参考网页，可能仅含通用入口，不能当作该日航班结果："]
        for item in leg.flights.items[:3]:
            title = item.title.replace("[", "（").replace("]", "）")
            lines += [f"- [{title}]({item.url}) · "
                      f"检索于 {item.fetched_at.astimezone(CHINA):%Y-%m-%d %H:%M}"]
        lines.append("")
    return "\n".join(lines)
