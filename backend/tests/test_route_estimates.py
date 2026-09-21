"""路线测试层：地图估时必须来自真实格式，坐标系和单位不能混用。"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.config import Settings
from app.schemas.document.answer import GeoPoint
from app.services.baidu import BaiduMaps

"""固定路线质量集：覆盖交通方式、单位换算、资料缺失和坐标系边界。"""

@pytest.mark.parametrize("case", json.loads(Path("evals/routes.v1.json").read_text(
    encoding="utf-8")), ids=lambda case: case["id"])
def test_fixed_route_quality(case):
    maps = BaiduMaps(Settings())
    maps.query = Mock(return_value={"status": 0, "result": {"routes": case["routes"]}})
    point = GeoPoint(longitude=120.1, latitude=30.1,
                     coordinate_system=case["coordinate_system"])
    result = maps.route(point, point, case["transport"])
    assert result.status == case["expected_status"]
    assert result.duration_minutes == case["expected_minutes"]
    if case["expected_model"] is None:
        maps.query.assert_not_called()
    else:
        assert maps.query.call_args.kwargs["model"] == case["expected_model"]

"""百度坐标按纬度在前传入，秒向上取整为分钟，不把距离当时长。"""

def test_route_estimate_uses_bd09_and_seconds():
    maps = BaiduMaps(Settings())
    maps.query = Mock(return_value={"status": 0, "result": {
        "routes": [{"duration": 1801, "distance": 2300}]}})
    origin = GeoPoint(longitude=120.1, latitude=30.1, coordinate_system="BD-09")
    target = GeoPoint(longitude=120.2, latitude=30.2, coordinate_system="BD-09")
    result = maps.route(origin, target, "walk")
    assert result.duration_minutes == 31 and result.distance_m == 2300
    assert result.status == "estimated" and result.checked_at
    maps.query.assert_called_once_with("map_directions", model="walking", origin="30.1,120.1",
        destination="30.2,120.2", is_chinese_mainland="true")


"""旧高德坐标不能直接交给百度；未查到路线时分钟数保持未知。"""

def test_route_unknown_or_wrong_coordinate_is_not_fabricated():
    maps = BaiduMaps(Settings())
    maps.query = Mock(return_value={"status": 0, "result": {"routes": []}})
    point = GeoPoint(longitude=120.1, latitude=30.1, coordinate_system="GCJ-02")
    assert maps.route(point, point, "walk").duration_minutes is None
    maps.query.assert_not_called()
    point = point.model_copy(update={"coordinate_system": "BD-09"})
    assert maps.route(point, point, "transit").status == "unavailable"


"""路线估时超过交通预留，候选必须返工；同一路线不重复请求。"""

def test_plan_route_check_rejects_short_transfer_and_reuses_evidence():
    import pytest

    from app.schemas.itinerary import RouteEstimate
    from app.services.itinerary.routes import verify_routes
    from app.services.itinerary.rules import build_plan
    from tests.test_itinerary_agent import places, proposal, requirements
    plan = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")], places(), None, None)
    # 此测试只核对逐段交通；日程总体约束由原build_plan测试覆盖。
    second = plan.days[1].activities[0].model_copy(update={"transfer_minutes": 30})
    plan.days[0].activities.append(second)
    for day in plan.days:
        for activity in day.activities:
            activity.place.map.location.coordinate_system = "BD-09"
    maps = Mock()
    maps.route.return_value = RouteEstimate(status="estimated", duration_minutes=61,
                                            distance_m=2500)
    cache = {}
    with pytest.raises(ValueError, match="61"):
        verify_routes(plan, maps, {1}, cache)
    plan.days[0].activities[1].transfer_minutes = 70
    verify_routes(plan, maps, {1}, cache)
    assert maps.route.call_count == 1
    assert plan.days[0].activities[1].route.duration_minutes == 61
