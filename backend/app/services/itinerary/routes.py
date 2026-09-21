"""行程核实层：查询相邻活动路线，对不足的交通预留要求返工；旧日程不重查。"""

from app.schemas.itinerary import RouteEstimate, TravelPlan
from app.services.baidu import BaiduMaps
from app.services.chat.events import check_cancelled, progress

"""路线核实函数：本次候选间复用查询结果，最多十段，避免返工反复访问地图。"""

def verify_routes(plan: TravelPlan, maps: BaiduMaps, target: set[int] | None,
                  cache: dict[str, RouteEstimate]) -> None:
    for day in plan.days:
        if target is not None and day.day not in target:
            continue
        for before, activity in zip(day.activities, day.activities[1:]):
            origin, destination = before.place.map.location, activity.place.map.location
            if origin is None or destination is None:
                continue
            if origin.coordinate_system != "BD-09" or destination.coordinate_system != "BD-09":
                activity.route = RouteEstimate(status="unavailable")
                continue
            key = f"{origin.model_dump_json()}:{destination.model_dump_json()}:{activity.transport}"
            if key not in cache:
                if len(cache) >= 10:
                    activity.route = RouteEstimate(status="unavailable")
                    continue
                progress("route", f"正在查询{before.place.map.name}到"
                         f"{activity.place.map.name}的路线")
                checked = maps.route(origin, destination, activity.transport)
                check_cancelled()
                cache[key] = checked
            activity.route = cache[key]
            if (activity.route.status == "estimated" and activity.route.duration_minutes is not None
                    and activity.transfer_minutes < activity.route.duration_minutes):
                raise ValueError(f"第{day.day}天前往{activity.place.map.name}的地图估时"
                    f"为{activity.route.duration_minutes}分钟，超过预留{activity.transfer_minutes}分钟；"
                    "请调整交通方式、活动时间或地点，超过两小时建议更换近处地点。")
