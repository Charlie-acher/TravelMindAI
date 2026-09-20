"""附件规划规则层：把已识别原文交给同一规划图，并核对用途、必经点与顺序。"""

from app.schemas.attachment import AttachmentSnapshot, AttachmentUse
from app.schemas.itinerary import PlanSource, TravelPlan
from app.schemas.requirement.base import TravelRequestExtraction

"""来源编号函数：同一附件的同一地点跨轮次保持编号，避免与知识库来源混淆。"""

def source_id(item: AttachmentSnapshot, index: int) -> str:
    return f"a{item.id.hex}_{index}"


"""单点定位函数：按旧计划中的完整地名确定目标日，重名或范围冲突时先补问。"""

def resolve_attachment_use(use: AttachmentUse, old: TravelPlan | None) -> AttachmentUse:
    if not use.target_places:
        return use
    if use.mode != "replace" or old is None:
        raise ValueError("请先确认原行程中要替换的景点。")
    days = set()
    for name in use.target_places:
        found = [day.day for day in old.days for activity in day.activities
                 if name in {activity.place.map.name, activity.place.map.matched_name}]
        if len(found) != 1:
            raise ValueError(f"原行程中无法唯一找到“{name}”，请说明完整景点名或目标日。")
        days.add(found[0])
    if use.target_days and days != set(use.target_days):
        raise ValueError("景点所在日期与指定目标日不一致，请确认要替换哪一天。")
    return use.model_copy(update={"target_days": sorted(days)})


"""附件准入函数：拒绝错误识别和跨城地点，限定替换范围，返回可追溯原文。"""

def prepare_attachments(items: list[AttachmentSnapshot], use: AttachmentUse,
                        requirements: TravelRequestExtraction,
                        old: TravelPlan | None) -> list[PlanSource]:
    if use.mode == "replace" and (old is None or not use.target_days):
        raise ValueError("替换需要已有行程和明确的目标日。请先生成行程，或说明替换第几天。")
    if use.target_days:
        if any(day > (requirements.days or 0) for day in use.target_days):
            raise ValueError("指定日期超出旅行天数，请说明要安排到第几天。")
        if old is not None and (len(old.days) != requirements.days
                or old.destination != requirements.destination
                or old.days[0].date != requirements.start_date):
            raise ValueError("目的地、日期或天数同时改变，无法仅替换指定日，请先确认全程范围。")
    sources = []
    for item in items:
        analysis = item.analysis
        if analysis is None:
            raise ValueError(f"{item.file_name}尚未成功识别，请补充清晰原件后重试。")
        if ((analysis.city is not None and analysis.city != requirements.destination)
                or (analysis.city is None and use.mode == "required")):
            raise ValueError(f"{item.file_name}的城市与目的地未能一致核对，请补充明确城市的附件。")
        if use.mode in {"required", "replace"} and not analysis.waypoints:
            raise ValueError(f"{item.file_name}没有可核对的地点，请补充具体地点或清晰路线。")
        if use.mode == "required" and any(
                warning.startswith("未确认地点：") for warning in analysis.warnings):
            raise ValueError(f"{item.file_name}有地点尚未看清，请先确认这些地点，或改为供参考。")
        for index, point in enumerate(analysis.waypoints):
            if len(point.name) > 80 or len(point.name) < 2 or any(
                    mark in point.name for mark in ("?", "？")):
                raise ValueError("附件中有不完整的地点名称，请补充清晰原件或准确名称。")
            sources.append(PlanSource(id=source_id(item, index), kind="attachment",
                attachment_id=item.id, title=item.file_name,
                text=f"{point.name}\n{point.evidence}"))
    if use.mode == "required" and len(sources) > 12:
        raise ValueError("本轮必经点超过12处，请拆分到多轮或缩小需要采用的路线范围。")
    return sources


"""必经校验函数：按附件来源核对覆盖及已知顺序，不让参考资料变成硬条件。"""

def validate_attachment_plan(plan: TravelPlan, items: list[AttachmentSnapshot],
                             use: AttachmentUse, *, old: TravelPlan | None = None) -> None:
    if use.mode not in {"required", "replace"}:
        return
    activities = [activity for day in plan.days
                  if not use.target_days or day.day in use.target_days
                  for activity in day.activities]
    if use.mode == "replace":
        if old is None:
            raise ValueError("替换需要已有行程。")
        previous_ids = {activity.place.id for day in old.days for activity in day.activities}
        candidates = [activity for activity in activities if activity.place.id not in previous_ids]
        matched = {activity.place.id for activity in activities if any(
            source.kind == "attachment" and source.attachment_id == item.id
            and source.id == source_id(item, index) and point.name == activity.place.map.name
            for item in items if item.analysis
            for index, point in enumerate(item.analysis.waypoints)
            for source in activity.place.sources)}
    if use.target_places and old is not None:
        for day in old.days:
            protected = [activity for activity in day.activities if not any(
                name in {activity.place.map.name, activity.place.map.matched_name}
                for name in use.target_places)]
            current = next(candidate for candidate in plan.days if candidate.day == day.day)
            unchanged = [activity for activity in current.activities
                         if any(activity.place.id == item.place.id for item in protected)]
            if unchanged != protected:
                raise ValueError("替换指定景点时，请保留其他活动及其时间安排。")
            if any(name in {activity.place.map.name, activity.place.map.matched_name}
                   for activity in current.activities for name in use.target_places):
                raise ValueError("指定景点尚未被替换，请用附件地点替换原活动。")
    if use.mode == "replace" and (
            not matched or any(activity.place.id not in matched for activity in candidates)):
        raise ValueError("替换景点须采用附件中有原文依据且已核对的地点。")
    for item in items:
        if item.analysis is None:
            raise ValueError("附件尚未完成识别，不能验证必经点")
        positions = []
        for index, point in enumerate(item.analysis.waypoints):
            position = next((i for i, activity in enumerate(activities)
                if activity.place.map.name == point.name
                and any(s.id == source_id(item, index) and s.attachment_id == item.id
                       for s in activity.place.sources)), None)
            if position is None:
                if use.mode == "replace":
                    continue
                raise ValueError(f"尚未安排附件必经点：{point.name}，请保留全部必经点或询问冲突。")
            positions.append(position)
        orders = [point.order for point in item.analysis.waypoints]
        if orders == list(range(1, len(orders) + 1)) and positions != sorted(positions):
            raise ValueError("附件路线的已知顺序未保留，请按原路线顺序安排。")
