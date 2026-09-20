"""附件规划测试层：验证私有证据准入、必经点与指定日修改边界。"""

import json
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.schemas.attachment import AttachmentAnalysis, AttachmentSnapshot, AttachmentUse
from app.services.itinerary.attachments import (
    prepare_attachments,
    resolve_attachment_use,
    source_id,
    validate_attachment_plan,
)
from app.services.itinerary.graph import plan_trip
from app.services.itinerary.rules import build_plan
from app.services.itinerary.tools import PlanTools
from tests.helpers import RecordingModel
from tests.test_itinerary_agent import places, proposal, requirements

"""附件样例函数：仅构造有明确城市、地点和原文的私人路线。"""

def route(*names, city="杭州"):
    return AttachmentSnapshot(id=uuid4(), file_name="路线.txt", analysis=AttachmentAnalysis(
        city=city, summary="旅行路线", waypoints=[dict(
            name=name, order=index + 1, evidence=f"杭州路线：{name}",
        ) for index, name in enumerate(names)],
    ))


"""必经校验函数：只有带附件来源的核实地点能满足必经条件，遗漏不能提交。"""

def test_required_points_need_map_evidence_and_cannot_be_omitted():
    items = [route("湖滨路步行街")]
    use = AttachmentUse(mode="required", apply_to_plan=True)
    tools = PlanTools("杭州", Mock(), Mock(), None, None)
    sources = prepare_attachments(items, use, requirements(), None)
    tools.sources.update({s.id: s for s in sources})
    plan = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                      places(), None, None)
    with pytest.raises(ValueError, match="必经"):
        validate_attachment_plan(plan, items, use)
    plan.days[0].activities[0].place.sources = [sources[0]]
    validate_attachment_plan(plan, items, use)
    assert sources[0].kind == "attachment" and sources[0].attachment_id == items[0].id


"""范围校验函数：不明城市、识别失败、无原草稿或不存在的目标日均先补问。"""

def test_attachment_scope_blocks_unsafe_changes():
    use = AttachmentUse(mode="replace", apply_to_plan=True, target_days=[2])
    with pytest.raises(ValueError, match="已有"):
        prepare_attachments([route("西湖")], use, requirements(), None)
    plan = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                      places(), None, None)
    for item in (route("拙政园", city="苏州"),
                 AttachmentSnapshot(id=uuid4(), file_name="坏图.png", error_message="模糊")):
        with pytest.raises(ValueError):
            prepare_attachments([item], use, requirements(), plan)
    with pytest.raises(ValueError, match="天"):
        prepare_attachments([route("西湖")], use.model_copy(update={"target_days": [3]}),
                            requirements(), plan)
    assert prepare_attachments([route("西湖", city=None)],
        AttachmentUse(mode="reference"), requirements(), None)
    with pytest.raises(ValueError, match="城市"):
        prepare_attachments([route("西湖", city=None)],
            AttachmentUse(mode="required"), requirements(), None)


"""单点修改测试函数：从旧计划定位被替换点，只修改它所在日并保留同日其他活动。"""

def test_replace_one_place_resolves_day_and_protects_other_activities():
    old = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                     places(), None, None)
    use = resolve_attachment_use(AttachmentUse(mode="replace", apply_to_plan=True,
        target_places=["河坊街"]), old)
    assert use.target_days == [2]
    with pytest.raises(ValueError, match="原行程"):
        resolve_attachment_use(AttachmentUse(mode="replace", target_places=["不在行程中"]), old)
    changed = old.model_copy(deep=True)
    changed.days[0].activities[0].start_time = "11:00"
    with pytest.raises(ValueError, match="其他"):
        validate_attachment_plan(changed, [], use, old=old)
    with pytest.raises(ValueError, match="尚未被替换"):
        validate_attachment_plan(old, [], use, old=old)


"""部分识别测试函数：参考可采用已确认点，必去及替换不能悄悄遗漏模糊地点。"""

def test_unconfirmed_points_cannot_be_silently_dropped_from_required_route():
    item = route("西湖")
    item.analysis.warnings = ["未确认地点：第2页的名称模糊"]
    assert prepare_attachments([item], AttachmentUse(mode="reference"), requirements(), None)
    with pytest.raises(ValueError, match="尚未看清"):
        prepare_attachments([item], AttachmentUse(mode="required"), requirements(), None)


"""单点取舍测试函数：只替换指定景点，不强制整篇攻略里已安排的地点重复入选。"""

def test_replace_one_place_selects_from_document_without_repeating_other_days():
    old = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                     places(), None, None)
    item = route("灵隐寺", "湖滨路步行街")
    use = resolve_attachment_use(AttachmentUse(mode="replace", apply_to_plan=True,
        target_places=["河坊街"]), old)
    changed = old.model_copy(deep=True)
    replacement = changed.days[1].activities[0].place
    replacement.id = "p3"
    replacement.map.name = "灵隐寺"
    replacement.map.poi_id = "poi3"
    replacement.sources = prepare_attachments([item], use, requirements(), old)[:1]
    validate_attachment_plan(changed, [item], use, old=old)
    assert changed.days[0] == old.days[0]
    replacement.sources = []
    with pytest.raises(ValueError, match="原文依据"):
        validate_attachment_plan(changed, [item], use, old=old)


"""参考校验函数：参考资料不要求所有地点入选，读取模式也不自动规划。"""

def test_reference_does_not_become_required():
    use = AttachmentUse(mode="reference")
    assert not use.apply_to_plan
    plan = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                      places(), None, None)
    validate_attachment_plan(plan, [route("未选择景点")], use)


"""同图测试函数：附件替换第二天，第一天、预算与旧对象保持不变。"""

def test_attachment_replace_uses_existing_graph_and_preserves_other_day():
    old = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                     places(), None, None)
    before = old.model_dump_json()
    item = route("河坊街")
    model = RecordingModel([json.dumps({"tools": [{"tool": "map_lookup", "name": "河坊街",
        "source_ids": [source_id(item, 0)]}]}),
        json.dumps({"days": [proposal(2, "p2", start="11:00").model_dump()]})])
    result = plan_trip("用附件替换第二天", requirements(), old, model.model, Mock(), Mock(), None,
        attachments=[item], attachment_use=AttachmentUse(
            mode="replace", apply_to_plan=True, target_days=[2]))
    assert result.plan is not None
    assert result.plan.days[0] == old.days[0]
    assert result.plan.days[1].activities[0].start_time == "11:00"
    assert result.plan.budget == old.budget
    assert old.model_dump_json() == before
    assert result.plan.days[1].activities[0].place.sources[0].attachment_id == item.id


"""假证据测试函数：不能将多个附件地点的来源挂到一个地图点上蒙混覆盖。"""

def test_attachment_source_cannot_verify_another_name():
    item = route("河坊街", "湖滨路步行街")
    tools = PlanTools("杭州", Mock(), Mock(), None, None)
    sources = prepare_attachments([item], AttachmentUse(mode="required"), requirements(), None)
    tools.sources.update({s.id: s for s in sources})
    result = tools.map_lookup("河坊街", [s.id for s in sources])
    assert "error" in result
    tools.maps.lookup.assert_not_called()


"""顺序校验函数：已知路线倒序和目标日之外的必经点均不能通过。"""

def test_attachment_order_and_target_day_are_enforced():
    item = route("河坊街", "湖滨路步行街")
    use = AttachmentUse(mode="required", apply_to_plan=True)
    sources = prepare_attachments([item], use, requirements(), None)
    plan = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                      places(), None, None)
    plan.days[0].activities[0].place.sources = [sources[1]]
    plan.days[1].activities[0].place.sources = [sources[0]]
    with pytest.raises(ValueError, match="顺序"):
        validate_attachment_plan(plan, [item], use)
    with pytest.raises(ValueError, match="必经"):
        validate_attachment_plan(plan, [item], use.model_copy(update={"target_days": [2]}))


"""范围冲突函数：模型不能把用户要求保持不变的日期纳入替换范围。"""

def test_attachment_target_cannot_expand_explicit_preserved_days():
    old = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                     places(), None, None)
    model = RecordingModel([json.dumps({"days": [proposal(1, "p1", start="11:00").model_dump(),
                                                proposal(2, "p2", start="11:00").model_dump()]})])
    result = plan_trip("第一天不变，用附件替换第二天", requirements(), old,
        model.model, Mock(), Mock(), None, attachments=[route("河坊街")],
        attachment_use=AttachmentUse(mode="replace", apply_to_plan=True, target_days=[1, 2]))
    assert result.plan is None
